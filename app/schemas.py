from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


ProviderName = Literal["deepseek", "openai"]


class ProviderUpdate(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)


class ProviderTest(BaseModel):
    model: str | None = Field(default=None, max_length=100)
    api_key: str | None = Field(default=None, min_length=8, max_length=500)


class ActiveProviderUpdate(BaseModel):
    provider: ProviderName


class ChatMessageInput(BaseModel):
    content: str = Field(min_length=1, max_length=8000)


class MessageFeedbackInput(BaseModel):
    rating: Literal["up", "down"] | None = None


class SessionTitleUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def title_must_have_text(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("会话标题不能为空")
        return value


class AdminSessionUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    management_status: Literal["open", "pending", "resolved"] | None = None
    assignee: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = Field(default=None, max_length=20)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = " ".join(value.split())
        if not value:
            raise ValueError("会话标题不能为空")
        return value

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return list(dict.fromkeys(tag.strip()[:30] for tag in value if tag.strip()))[:20]


class SessionNoteInput(BaseModel):
    content: str = Field(min_length=1, max_length=2000)


class PromptDraftInput(BaseModel):
    content: str = Field(min_length=20, max_length=20000)
    change_note: str = Field(default="", max_length=300)


class PromptRollbackInput(BaseModel):
    change_note: str = Field(default="", max_length=300)


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=6, ge=1, le=20)


class ContactAdminUpdate(BaseModel):
    status: Literal["new", "following", "closed"] | None = None
    note: str | None = Field(default=None, max_length=2000)


class KnowledgePathInput(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    recursive: bool = True


class KnowledgeTermsUpdate(BaseModel):
    groups: list[list[str]] = Field(default_factory=list, max_length=100)

    @field_validator("groups")
    @classmethod
    def validate_groups(cls, groups: list[list[str]]) -> list[list[str]]:
        normalized: list[list[str]] = []
        for group in groups:
            if len(group) > 20:
                raise ValueError("每组同义词最多 20 个")
            values = [" ".join(value.split()) for value in group if value.strip()]
            if any(len(value) > 80 for value in values):
                raise ValueError("单个术语不能超过 80 个字符")
            if len(values) >= 2:
                normalized.append(values)
        return normalized


class ContactInput(BaseModel):
    session_id: str | None = None
    name: str = Field(min_length=1, max_length=80)
    phone: str | None = Field(default=None, pattern=r"^1\d{10}$")
    email: EmailStr | None = None
    summary: str = Field(min_length=2, max_length=1000)

    @model_validator(mode="after")
    def require_contact(self) -> "ContactInput":
        if not self.phone and not self.email:
            raise ValueError("手机号或邮箱至少填写一个")
        return self
