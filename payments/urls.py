from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("checkout/<int:enrollment_id>/", views.checkout, name="checkout"),
    path("success/<int:enrollment_id>/", views.success, name="success"),
    path("callback/flutterwave/<int:payment_id>/", views.flutterwave_callback,
         name="flutterwave_callback"),
    path("webhook/flutterwave/", views.flutterwave_webhook, name="flutterwave_webhook"),
    path("callback/kkiapay/<int:payment_id>/", views.kkiapay_callback, name="kkiapay_callback"),
    path("webhook/kkiapay/<int:payment_id>/", views.kkiapay_webhook, name="kkiapay_webhook"),
    path("webhook/kkiapay/", views.kkiapay_webhook, name="kkiapay_webhook_generic"),
    path("check/<int:payment_id>/", views.check_payment_status, name="check_status"),
]
