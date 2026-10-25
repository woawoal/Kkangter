"""데이터 모델.

핵심 아이디어
- 인형은 `Series`(시리즈) > `Character`(캐릭터) 2단계 카탈로그로 관리한다.
  → 검색/필터/알림/시세가 모두 character_id 하나로 연결된다.
- 게시글 하나에 여러 인형이 들어갈 수 있다(일괄 양도, 2:1 교환 등).
  `PostItem.role` 로 '가진 것(have)'과 '원하는 것(want)'을 구분한다.
    양도  = have 여러 개 (+ 개별 가격)
    교환  = have 여러 개 + want 여러 개
    구해요 = want 여러 개 (+ 희망 가격)
- 모든 핵심 행동은 `Event` 에 남겨 포트폴리오 지표로 쓴다.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


POST_TYPES = {"sell": "양도", "trade": "교환", "want": "구해요"}
POST_STATUS = {"open": "거래중", "reserved": "예약중", "done": "거래완료", "hidden": "숨김"}


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(20), default="kakao")  # kakao | dev
    provider_id: Mapped[str] = mapped_column(String(64))
    nickname: Mapped[str] = mapped_column(String(30), default="")
    openchat_url: Mapped[str] = mapped_column(String(300), default="")
    kakao_talk_id: Mapped[str] = mapped_column(String(50), default="")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    __table_args__ = (UniqueConstraint("provider", "provider_id"),)

    @property
    def onboarded(self) -> bool:
        return bool(self.nickname and (self.openchat_url or self.kakao_talk_id))


class Series(Base):
    __tablename__ = "series"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    brand: Mapped[str] = mapped_column(String(50), default="")
    aliases: Mapped[str] = mapped_column(Text, default="")  # 검색용 별칭 "라부부,labubu,더몬스터즈"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    characters: Mapped[list["Character"]] = relationship(
        back_populates="series", order_by="Character.sort, Character.id"
    )


class Character(Base):
    __tablename__ = "characters"
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    aliases: Mapped[str] = mapped_column(Text, default="")
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)  # 시크릿(희귀) 여부
    sort: Mapped[int] = mapped_column(Integer, default=0)
    series: Mapped[Series] = relationship(back_populates="characters")

    @property
    def label(self) -> str:
        return f"{self.series.name} · {self.name}"


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    type: Mapped[str] = mapped_column(String(10), index=True)  # sell | trade | want
    status: Mapped[str] = mapped_column(String(10), default="open", index=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 전체 가격(원) 또는 희망가
    note: Mapped[str] = mapped_column(Text, default="")
    ship_method: Mapped[str] = mapped_column(String(30), default="")  # 반값택배/GS/직거래 등
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    # 정렬 기준. 끌어올리기(유료화 1순위)는 이 값만 갱신하면 된다.
    bumped_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped[User] = relationship()
    items: Mapped[list["PostItem"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="PostItem.id"
    )
    photos: Mapped[list["PostPhoto"]] = relationship(
        back_populates="post", cascade="all, delete-orphan", order_by="PostPhoto.sort"
    )

    @property
    def have_items(self):
        return [i for i in self.items if i.role == "have"]

    @property
    def want_items(self):
        return [i for i in self.items if i.role == "want"]

    @property
    def type_label(self):
        return POST_TYPES.get(self.type, self.type)

    @property
    def status_label(self):
        return POST_STATUS.get(self.status, self.status)

    @property
    def title(self) -> str:
        items = self.have_items or self.want_items
        if not items:
            return self.type_label
        first = items[0].character.name
        return first if len(items) == 1 else f"{first} 외 {len(items) - 1}개"


class PostItem(Base):
    __tablename__ = "post_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    character_id: Mapped[int] = mapped_column(ForeignKey("characters.id"), index=True)
    role: Mapped[str] = mapped_column(String(5))  # have | want
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 개별 가격 (시세 데이터 원천)
    post: Mapped[Post] = relationship(back_populates="items")
    character: Mapped[Character] = relationship()

    __table_args__ = (Index("ix_item_char_role", "character_id", "role"),)


class PostPhoto(Base):
    __tablename__ = "post_photos"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(400))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    post: Mapped[Post] = relationship(back_populates="photos")


class Watch(Base):
    """'이 인형 올라오면 알려주세요'. character 단위(정확) 또는 series 단위(시리즈 전체)."""
    __tablename__ = "watches"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    character_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id"), nullable=True, index=True)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"), nullable=True, index=True)
    # 어떤 글이 올라오면 알릴지: "sell,trade" (구해요 글을 보고 싶은 판매자는 "want")
    post_types: Mapped[str] = mapped_column(String(30), default="sell,trade")
    max_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    character: Mapped[Character | None] = relationship()
    series: Mapped[Series | None] = relationship()

    @property
    def target_label(self) -> str:
        if self.character:
            return self.character.label
        if self.series:
            return f"{self.series.name} · 전체"
        return "-"


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    watch_id: Mapped[int | None] = mapped_column(ForeignKey("watches.id", ondelete="SET NULL"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="watch")  # watch | mutual_trade
    message: Mapped[str] = mapped_column(String(200), default="")
    pushed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    post: Mapped[Post] = relationship()

    __table_args__ = (UniqueConstraint("user_id", "post_id", "kind"),)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(300))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    post: Mapped[Post] = relationship()


class BetaSignup(Base):
    __tablename__ = "beta_signups"
    id: Mapped[int] = mapped_column(primary_key=True)
    nickname: Mapped[str] = mapped_column(String(30))
    contact: Mapped[str] = mapped_column(String(120))  # 오픈톡 닉네임 / 카톡ID / 이메일
    interests: Mapped[str] = mapped_column(String(300), default="")
    roles: Mapped[str] = mapped_column(String(50), default="")  # sell,trade,want
    source: Mapped[str] = mapped_column(String(50), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Event(Base):
    """제품 지표용 이벤트 로그. 대시보드와 포트폴리오 수치의 단일 출처."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    post_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    props: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
