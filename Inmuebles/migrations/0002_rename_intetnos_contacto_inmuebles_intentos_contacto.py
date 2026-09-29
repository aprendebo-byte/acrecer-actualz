from django.db import migrations


class Migration(migrations.Migration):
    """Corrige el typo del campo: intetnos_contacto -> intentos_contacto."""

    dependencies = [
        ('Inmuebles', '0001_initial'),
    ]

    operations = [
        migrations.RenameField(
            model_name='inmuebles',
            old_name='intetnos_contacto',
            new_name='intentos_contacto',
        ),
    ]
