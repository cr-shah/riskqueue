import json

import pytest

from riskqueue.cloud.lambda_handler import lambda_handler


class FakeQueue:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return f"message-{len(self.messages)}"


def test_lambda_translates_s3_notification_to_processing_message():
    queue = FakeQueue()
    event = {
        "Records": [
            {
                "eventSource": "aws:s3",
                "eventTime": "2026-09-23T12:00:00Z",
                "s3": {
                    "bucket": {"name": "riskqueue-raw"},
                    "object": {"key": "incoming%2Fbatch+1.csv", "eTag": "etag-1"},
                },
            }
        ]
    }

    response = lambda_handler(event, None, queue=queue)

    assert response["statusCode"] == 202
    assert json.loads(response["body"])["accepted"] == 1
    assert queue.messages[0].object.key == "incoming/batch 1.csv"
    assert queue.messages[0].source == "s3-notification"


def test_lambda_rejects_non_s3_records():
    with pytest.raises(ValueError, match="Only S3"):
        lambda_handler({"Records": [{"eventSource": "aws:sns"}]}, None, queue=FakeQueue())
