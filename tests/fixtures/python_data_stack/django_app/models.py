from django.db import models


class Customer(models.Model):
    name = models.CharField(max_length=120)

    class Meta:
        db_table = "crm_customer"
