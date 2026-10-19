from django.contrib import admin

from .models import Enrollment, Payment, PaymentPlan


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ("provider", "amount", "currency", "status", "tranche_index",
                       "provider_ref", "created_at", "verified_at")


@admin.register(PaymentPlan)
class PaymentPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "currency", "notion_count", "max_installments", "active")
    list_filter = ("active", "currency")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "plan", "status", "created_at")
    list_filter = ("status", "plan")
    search_fields = ("student__username", "student__email")
    inlines = [PaymentInline]
    readonly_fields = ("status", "created_at")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("enrollment", "provider", "amount", "tranche_index", "status",
                    "created_at", "verified_at")
    list_filter = ("provider", "status")
    search_fields = ("enrollment__student__username", "provider_ref")
    readonly_fields = tuple(f.name for f in Payment._meta.fields)
