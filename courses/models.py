from django.db import models

LEVEL_CHOICES = [
    ("A1", "A1"), ("A2", "A2"),
    ("B1", "B1"), ("B2", "B2"),
    ("C1", "C1"), ("C2", "C2"),
]


class Notion(models.Model):
    """A single teaching unit within a level. Ordered sequentially."""
    level = models.CharField(max_length=2, choices=LEVEL_CHOICES)
    title = models.CharField(max_length=255)
    order = models.PositiveIntegerField(help_text="Position within the level, 1-indexed")

    class Meta:
        ordering = ["level", "order"]
        unique_together = ("level", "order")

    def __str__(self):
        return f"[{self.level}] {self.order}. {self.title}"

    def is_first_in_level(self):
        return self.order == 1

    def previous(self):
        return Notion.objects.filter(level=self.level, order=self.order - 1).first()

    def next(self):
        return Notion.objects.filter(level=self.level, order=self.order + 1).first()


class PDFResource(models.Model):
    notion = models.ForeignKey(Notion, on_delete=models.CASCADE, related_name="pdfs")
    file = models.FileField(upload_to="notions/pdfs/%Y/%m/")
    title = models.CharField(max_length=255, blank=True)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]


class AudioResource(models.Model):
    POSITION_CHOICES = [("top", "Haut"), ("middle", "Milieu"), ("bottom", "Bas")]

    notion = models.ForeignKey(Notion, on_delete=models.CASCADE, related_name="audios")
    file = models.FileField(upload_to="notions/audio/%Y/%m/")
    title = models.CharField(max_length=255, blank=True)
    position = models.CharField(max_length=10, choices=POSITION_CHOICES, default="top")
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order"]
