from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def config(connection):
    settings = Config("alembic.ini")
    settings.attributes["connection"] = connection
    return settings


def test_fresh_database_upgrades_to_latest(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}")
    with engine.connect() as connection:
        command.upgrade(config(connection), "head")
        assert {
            "processed_events",
            "operational_transactions",
            "capacity_periods",
            "cases",
            "case_audit",
            "case_outcomes",
        }.issubset(inspect(connection).get_table_names())
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0002"
        )


def test_existing_schema_and_data_upgrade_in_place(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'existing.db'}")
    with engine.connect() as connection:
        command.upgrade(config(connection), "0001")
        connection.execute(
            text(
                "INSERT INTO processed_events (event_id,idempotency_key,batch_id,source,object_bucket,object_key,status,started_at,transaction_count) VALUES ('old-event','key','old-batch','test','raw','old.csv','completed','2025-01-01',1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO operational_transactions (transaction_id,event_id,ingested_at,step,transaction_type,amount,sender_id,recipient_id,fraud_probability,expected_loss,risk_band,decision,model_version) VALUES ('old-tx','old-event','2025-01-01',1,'TRANSFER',100,'A','B',0.5,50,'high','review','old-model')"
            )
        )
        connection.commit()
        command.upgrade(config(connection), "head")
        row = connection.execute(
            text(
                "SELECT transaction_id,model_version,feature_version,policy_version,feature_snapshot FROM operational_transactions WHERE transaction_id='old-tx'"
            )
        ).one()
        assert tuple(row) == ("old-tx", "old-model", "legacy-unknown", "legacy-unknown", None)
        assert (
            connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0002"
        )
        assert "ix_operational_sender_step" in {
            index["name"] for index in inspect(connection).get_indexes("operational_transactions")
        }
