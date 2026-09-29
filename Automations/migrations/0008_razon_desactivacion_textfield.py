from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("Automations", "0007_indices_historial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="historialrespuestas",
            name="razon_desactivacion",
            field=models.TextField(blank=True, null=True),
        ),
    ]
