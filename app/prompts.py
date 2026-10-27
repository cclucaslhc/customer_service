from app.database import utc_now
from app.persistence.config_repository import LocalConfigurationRepository
from app.prompting import DEFAULT_BUSINESS_PROMPT, SAFETY_BASELINE, combined_instructions


class PromptService:
    def __init__(self, repository=None):
        self.repository = repository or LocalConfigurationRepository()

    def active_prompt(self) -> dict:
        row = self.repository.active_prompt()
        if not row:
            return {"id": None, "version": 0, "content": DEFAULT_BUSINESS_PROMPT, "status": "legacy"}
        return dict(row)

    def runtime_prompt(self) -> tuple[str | None, str]:
        current = self.active_prompt()
        return current.get("id"), combined_instructions(current["content"])

    def list_prompts(self) -> dict:
        versions = self.repository.list_prompts()
        return {"baseline": SAFETY_BASELINE, "current": next((item for item in versions if item["status"] == "published"), None), "versions": versions}

    def create_draft(self, content: str, change_note: str) -> dict:
        return self.repository.create_draft(content=content, change_note=change_note, now=utc_now())

    def publish(self, prompt_id: str) -> dict | None:
        return self.repository.publish_prompt(prompt_id, utc_now())

    def rollback(self, source_id: str, change_note: str) -> dict | None:
        return self.repository.rollback_prompt(source_id, change_note, utc_now())


repository = LocalConfigurationRepository()


def active_prompt() -> dict:
    return PromptService(repository).active_prompt()


def runtime_prompt() -> tuple[str | None, str]:
    return PromptService(repository).runtime_prompt()


def list_prompts() -> dict:
    return PromptService(repository).list_prompts()


def create_draft(content: str, change_note: str) -> dict:
    return PromptService(repository).create_draft(content, change_note)


def publish(prompt_id: str) -> dict | None:
    return PromptService(repository).publish(prompt_id)


def rollback(source_id: str, change_note: str) -> dict | None:
    return PromptService(repository).rollback(source_id, change_note)
