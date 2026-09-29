from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Índices en las tablas de historial.

    Se filtra por `codigo_inmueble` y se ordena por `fecha_registro` en cada
    desactivación y en la generación del informe.
    """

    dependencies = [
        ('Automations', '0006_envioplantillalog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historialrespuestas',
            name='fecha_registro',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='historialrespuestas',
            name='codigo_inmueble',
            field=models.CharField(db_index=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='historialactualizacionesprecios',
            name='fecha_registro',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='historialactualizacionesprecios',
            name='codigo_inmueble',
            field=models.CharField(db_index=True, max_length=100),
        ),
        migrations.AlterField(
            model_name='historialintervenciones',
            name='fecha_registro',
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AlterField(
            model_name='historialintervenciones',
            name='codigo_inmueble',
            field=models.CharField(db_index=True, max_length=100),
        ),
    ]
