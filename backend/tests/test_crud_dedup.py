import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.models import Base, ConversationEntity
from app.db.crud import save_entity
import uuid

@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()

def test_dedup_exact_normalization(db_session):
    """Test that safe string normalization catches exact matches regardless of case/whitespace."""
    conv_id = "test_conv"
    entity_id = str(uuid.uuid4())
    
    # Save the original
    save_entity(
        db_session, conv_id, "Priya's email", "person", entity_id, {"email": "priya@example.com"}
    )
    
    # Save slightly different capitalization/whitespace
    save_entity(
        db_session, conv_id, "  priya'S email  ", "person", entity_id, {"email": "priya@example.com"}
    )
    
    count = db_session.query(ConversationEntity).count()
    assert count == 1  # Should upsert!

def test_dedup_negative_distinct_entities(db_session):
    """Test that similar but distinct labels do not falsely merge into one entity."""
    conv_id = "test_conv"
    
    save_entity(
        db_session, conv_id, "Priya's email", "person", str(uuid.uuid4()), {"val": "1"}
    )
    save_entity(
        db_session, conv_id, "Priya's phone", "person", str(uuid.uuid4()), {"val": "2"}
    )
    
    count = db_session.query(ConversationEntity).count()
    assert count == 2  # Should be 2 distinct entities!
