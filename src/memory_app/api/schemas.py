from typing import Annotated, Literal
from pydantic import AfterValidator, BaseModel, Field, StrictInt, StringConstraints

Nonempty = Annotated[str, StringConstraints(min_length=1)]


def nonblank_identifier(value: str) -> str:
    if not value.strip():
        raise ValueError("identifier must not be blank")
    # Preserve the received value; never silently trim isolation identifiers.
    return value


Identifier = Annotated[Nonempty, AfterValidator(nonblank_identifier)]


class TextPart(BaseModel):
    type: Literal["text"]
    text: Nonempty


class ImageURL(BaseModel):
    url: Nonempty


class ImagePart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageURL


Part = Annotated[TextPart | ImagePart, Field(discriminator="type")]
Content = Nonempty | Annotated[list[Part], Field(min_length=1)]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: Content
    timestamp: StrictInt | None = None


class AddRequest(BaseModel):
    request_id: Identifier
    user_id: Identifier
    session_id: Identifier
    messages: list[Message] = Field(min_length=1)

class SearchRequest(BaseModel):
    query: Content
    user_id: Identifier
    top_k: Annotated[StrictInt, Field(ge=0, le=100)]
    options: list[str] | None = None
