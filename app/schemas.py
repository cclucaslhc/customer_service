from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


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
