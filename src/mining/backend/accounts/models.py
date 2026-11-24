"""User and customer records."""

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


class UserManager(BaseUserManager):
    """Create users identified by their email address."""

    use_in_migrations = True

    def create_user(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> "User":
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email: str, password: str | None = None, **extra_fields: object
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("role", User.Role.SUPER_ADMIN)

        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("A superuser must have staff and superuser status.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Application user with a role used by server-side authorization."""

    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super admin"
        LIVESELLER_ADMIN = "LIVESELLER_ADMIN", "Live seller admin"
        ORDERS_ADMIN = "ORDERS_ADMIN", "Orders admin"
        PACKING_ADMIN = "PACKING_ADMIN", "Packing admin"
        FINANCE_ADMIN = "FINANCE_ADMIN", "Finance admin"

    username = None
    first_name = None
    last_name = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=255)
    role = models.CharField(
        max_length=32, choices=Role.choices, default=Role.LIVESELLER_ADMIN
    )

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    def __str__(self) -> str:
        return self.name


class Customer(models.Model):
    """A buyer identified by their Facebook account."""

    facebook_id = models.CharField(max_length=255, unique=True)
    full_name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.full_name
