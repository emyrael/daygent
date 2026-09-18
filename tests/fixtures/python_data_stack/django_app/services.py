from django_app.models import Customer


def get_active_customers():
    return Customer.objects.filter(active=True)


def create_customer():
    return Customer.objects.create(name="Ada")


def active_customers_raw():
    return Customer.objects.raw(
        """
        SELECT *
        FROM crm_customer
        WHERE active = true
        """
    )
