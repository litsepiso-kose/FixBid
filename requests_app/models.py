from django.db import models

#Models

SERVICE_CHOICES = [
    ("plumbing", "Plumbing"),
    ("electrical", "Electrical"),
    ("painting", "Painting"),
    ("cleaning", "Cleaning"),
    ("carpentry", "Carpentry"),
]


class Customer(models.Model):
    name = models.CharField(max_length= 100)
    phone_number = models.CharField(max_length=20, unique=True)

    def __str__(self):
        return self.name
    


class Provider(models.Model):
    name = models.CharField(max_length = 100)
    phone_number = models.CharField(max_length=20, unique= True)
    service_type = models.CharField(max_length=20,choices= SERVICE_CHOICES )
    location = models.CharField(max_length= 100)
    province = models.CharField(max_length=30, blank=True)

    def __str__(self):
        return self.name
    

class ServiceRequest(models.Model):

    STATUS_CHOICES = [
        ("open","Open"),
        ("quoted","Quoted"),
        ("matched","Matched"),
        ("completed","Completed"),
        ("problems","Problems"),
    ]

    customer = models.ForeignKey(
        Customer, 
        on_delete = models.PROTECT,
        related_name= "service_requests"
    )

    service_type = models.CharField(max_length=100, choices= SERVICE_CHOICES)
    description = models.TextField()
    location = models.CharField(max_length = 100)
    province = models.CharField(max_length=30, blank=True)
    media_url = models.URLField(max_length=2000, blank=True)
    media_type = models.CharField(max_length=100, blank=True)

    source_message_sid = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices= STATUS_CHOICES,
        default = "open"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.service_type} - {self.customer.name}"


class Conversation(models.Model):
    STEP_CHOICES = [
        ("service", "Choosing service"),
        ("province", "Choosing province"),
        ("suburb", "Entering suburb"),
        ("details", "Providing details"),
        ("confirm", "Confirming request"),
    ]

    customer = models.OneToOneField(
        Customer,
        on_delete=models.CASCADE,
        related_name="conversation",
    )

    step = models.CharField(
        max_length=20,
        choices=STEP_CHOICES,
        default="service",
    )

    service_type = models.CharField(
        max_length=20,
        choices=SERVICE_CHOICES,
        blank=True,
    )

    province = models.CharField(max_length=30, blank=True)
    suburb = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    media_url = models.URLField(max_length=2000, blank=True)
    media_type = models.CharField(max_length=100, blank=True)

    updated_at = models.DateTimeField(auto_now=True) 

    def __str__(self):
        return f"{self.customer.name} - {self.step}"

class Quote(models.Model):
    service_request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.CASCADE,
        related_name="quotes",
    )

    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        related_name="quotes",
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    customer_notified_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    availability = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service_request", "provider"],
                name="one_quote_per_provider_per_request",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gt=0),
                name="quote_price_positive",
            ),
        ]

    def __str__(self):
        return (
            f"{self.provider.name} - "
            f"Request #{self.service_request_id} - R{self.price}"
        )


class JobInvitation(models.Model):
    service_request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.CASCADE,
        related_name="invitations",
    )

    provider = models.ForeignKey(
        Provider,
        on_delete=models.PROTECT,
        related_name="invitations",
    )

    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["service_request", "provider"],
                name="one_invitation_per_provider_per_request",
            )
        ]

    def __str__(self):
        return (
            f"Request #{self.service_request_id} - "
            f"{self.provider.name}"
        )

class ProviderConversation(models.Model):
    provider = models.OneToOneField(
        Provider,
        on_delete=models.CASCADE,
        related_name="conversation",
    )

    step = models.CharField(max_length=20, default="job")

    job_options = models.JSONField(default=list, blank=True)

    service_request = models.ForeignKey(
        ServiceRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    availability = models.CharField(max_length=200, blank=True)