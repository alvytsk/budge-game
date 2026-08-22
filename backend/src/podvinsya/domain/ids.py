from typing import NewType
from uuid import UUID

MatchId = NewType("MatchId", UUID)
PlayerId = NewType("PlayerId", UUID)
GroupId = NewType("GroupId", UUID)
CategoryId = NewType("CategoryId", UUID)
ImageId = NewType("ImageId", UUID)
