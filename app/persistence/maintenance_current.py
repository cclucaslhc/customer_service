"""Current sealing inventory; never mutate the immutable 0017 migration input."""
from app.persistence.maintenance_v1 import PROTECTED_TABLES as ORIGINAL_TABLES, WRITE_GATE_KEY

PROTECTED_TABLES = (*ORIGINAL_TABLES, 'model_usage_attempts','model_price_versions','model_price_quotes','model_cost_assessments',
                    'model_budget_policies','model_budget_reservations','model_route_budget_policies',
                    'model_circuit_state','embedding_profiles','knowledge_index_versions',
                    'knowledge_chunk_versions','knowledge_embeddings_1024','knowledge_admission_policy',
                    'rag_release_intents','rag_rollouts')
