from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from riskqueue.cloud.messages import ProcessingMessage


@dataclass(frozen=True)
class ReceivedMessage:
    body: str
    receipt_handle: str
    message_id: str
    receive_count: int


class ProcessingQueue(Protocol):
    def send(self, message: ProcessingMessage) -> str: ...


class SQSProcessingQueue:
    def __init__(self, queue_url: str | None = None, client=None):
        self.queue_url = queue_url or os.environ["PROCESSING_QUEUE_URL"]
        if client is None:
            import boto3

            client = boto3.client("sqs", region_name=os.getenv("AWS_REGION"))
        self.client = client

    def send(self, message: ProcessingMessage) -> str:
        response = self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=message.model_dump_json(),
            MessageAttributes={
                "schema_version": {"DataType": "String", "StringValue": message.schema_version},
                "event_id": {"DataType": "String", "StringValue": message.event_id},
            },
        )
        return response["MessageId"]

    def receive(self, *, wait_seconds: int = 20, max_messages: int = 10) -> list[ReceivedMessage]:
        response = self.client.receive_message(
            QueueUrl=self.queue_url,
            WaitTimeSeconds=wait_seconds,
            MaxNumberOfMessages=max_messages,
            AttributeNames=["ApproximateReceiveCount"],
        )
        return [
            ReceivedMessage(
                body=item["Body"],
                receipt_handle=item["ReceiptHandle"],
                message_id=item["MessageId"],
                receive_count=int(item.get("Attributes", {}).get("ApproximateReceiveCount", "1")),
            )
            for item in response.get("Messages", [])
        ]

    def delete(self, receipt_handle: str) -> None:
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=receipt_handle)
