from django.contrib import admin

from .models import Notion, PDFResource, AudioResource


class PDFResourceInline(admin.TabularInline):
    model = PDFResource
    extra = 1


class AudioResourceInline(admin.TabularInline):
    model = AudioResource
    extra = 1


@admin.register(Notion)
class NotionAdmin(admin.ModelAdmin):
    list_display = ("title", "level", "order")
    list_filter = ("level",)
    inlines = [PDFResourceInline, AudioResourceInline]
