"""Master model M22: the customer, and the commercial consequence of a late order."""

from typing import Optional

from sqlalchemy import CHAR, CheckConstraint, Enum, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.master import CustomerPriorityTier
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Money, MoneyLarge, Percent


class Customer(SoftDeleteMixin, TimestampCreatedMixin,
               TimestampUpdatedMixin, Base):
    """Table ``customer``, master group: who the output is for, and the penalty and
    priority a recommendation's business impact is argued from."""

    __tablename__ = "customer"
    __table_args__ = (
        UniqueConstraint("customer_code", name="uq_customer_code"),
        CheckConstraint("customer_code GLOB 'CUS-[0-9][0-9][0-9]'",
                        name=conv("ck_customer_code_format")),
        CheckConstraint("country_code GLOB '[A-Z][A-Z]'",
                        name=conv("ck_customer_country_code_format")),
        CheckConstraint(
            "late_delivery_penalty_per_day IS NULL "
            "OR late_delivery_penalty_per_day >= 0",
            name=conv("ck_customer_penalty_non_negative")),
        CheckConstraint(
            "contractual_otd_target_pct IS NULL "
            "OR contractual_otd_target_pct BETWEEN 0 AND 100",
            name=conv("ck_customer_otd_target_range")),
        CheckConstraint(
            "annual_order_value IS NULL OR annual_order_value > 0",
            name=conv("ck_customer_annual_value_positive")),
        CheckConstraint(
            "contact_email IS NULL OR (contact_email GLOB '?*@?*.?*' "
            "AND contact_email NOT GLOB '*@*@*')",
            name=conv("ck_customer_email_format")),
        CheckConstraint("length(trim(customer_name)) > 0",
                        name=conv("ck_customer_name_not_blank")),
        CheckConstraint("priority_tier IN ('gold', 'silver', 'bronze')",
                        name=conv("ck_customer_priority_tier_allowed")),
        CheckConstraint("length(customer_code) <= 10",
                        name=conv("ck_customer_customer_code_length")),
        CheckConstraint("length(customer_name) <= 150",
                        name=conv("ck_customer_customer_name_length")),
        CheckConstraint(
            "industry_sector IS NULL OR length(industry_sector) <= 80",
            name=conv("ck_customer_industry_sector_length")),
        CheckConstraint("length(city) <= 80",
                        name=conv("ck_customer_city_length")),
        CheckConstraint("length(country_code) = 2",
                        name=conv("ck_customer_country_code_length")),
        CheckConstraint(
            "contact_person IS NULL OR length(contact_person) <= 100",
            name=conv("ck_customer_contact_person_length")),
        CheckConstraint(
            "contact_email IS NULL OR length(contact_email) <= 150",
            name=conv("ck_customer_contact_email_length")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_customer_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    customer_id: Mapped[MasterPk]
    customer_code: Mapped[str] = mapped_column(String(10))
    customer_name: Mapped[str] = mapped_column(String(150))
    priority_tier: Mapped[CustomerPriorityTier] = mapped_column(
        Enum(CustomerPriorityTier, name="customer_priority_tier",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    industry_sector: Mapped[Optional[str]] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(80))
    country_code: Mapped[str] = mapped_column(CHAR(2))
    contact_person: Mapped[Optional[str]] = mapped_column(String(100))
    contact_email: Mapped[Optional[str]] = mapped_column(String(150))
    late_delivery_penalty_per_day: Mapped[Optional[Money]]
    contractual_otd_target_pct: Mapped[Optional[Percent]]
    # MoneyLarge rather than Money: an annual aggregate needs NUMERIC(14,2) and
    # this is the only column at that precision (§38.4).
    annual_order_value: Mapped[Optional[MoneyLarge]]

    # No relationships are mapped. Customer holds no foreign keys, and its only
    # inbound reference -- production_run -- is unmapped under rule L1 (§19.1).
    # ProductionRun.customer is unidirectional and declares no back_populates.

    @validates("customer_code", "country_code")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("contact_email")
    def _normalise_email(self, key: str, value: Optional[str]) -> Optional[str]:
        return value if value is None else value.strip().lower()
