import os

from django.utils import timezone
from twilio.rest import Client

from .models import Provider, JobInvitation, ProviderConversation, ServiceRequest, Quote

from decimal import Decimal, InvalidOperation

from django.db import transaction




def find_matching_providers(service_request):
    return Provider.objects.filter(
        service_type=service_request.service_type,
        province__iexact=service_request.province.strip(),
        location__iexact=service_request.location.strip(),
    )

def send_job_invitation(service_request, provider):
    if not find_matching_providers(service_request).filter(
        pk=provider.pk
    ).exists():
        raise ValueError("Provider does not match this request.")

    invitation, _ = JobInvitation.objects.get_or_create(
        service_request=service_request,
        provider=provider,
    )

    if invitation.sent_at:
        return "Invitation already submitted."

    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    )

    message = client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"],
        to=f"whatsapp:{provider.phone_number}",
        body=(
            f"FixBid job #{service_request.pk}\n\n"
            f"Service: {service_request.get_service_type_display()}\n"
            f"Area: {service_request.location}, "
            f"{service_request.province}\n"
            f"Problem: {service_request.description}\n\n"
            "To bid, reply in this format:\n"
            f"QUOTE {service_request.pk} | 500 | Tomorrow at 9am"
        ),
    )

    invitation.sent_at = timezone.now()
    invitation.save(update_fields=["sent_at"])

    return message.sid

def handle_quote(phone_number, incoming_message):
    provider = Provider.objects.filter(
        phone_number=phone_number
    ).first()

    if not provider:
        return "Your number is not registered as a provider."

    parts = incoming_message.split("|", 2)

    if len(parts) != 3:
        return "Use this format: QUOTE 1 | 500 | Tomorrow at 9am"

    command_parts = parts[0].split()

    if (
        len(command_parts) != 2
        or command_parts[0].upper() != "QUOTE"
        or not command_parts[1].isdigit()
        or len(command_parts[1]) > 10
    ):
        return "Start with QUOTE followed by the job number."

    request_id = int(command_parts[1])
    availability = parts[2].strip()

    try:
        price = Decimal(parts[1].strip())
    except InvalidOperation:
        return "Enter a numeric price, for example: 500 or 500.50"

    if not price.is_finite() or not Decimal("0") < price <= Decimal("99999999.99"):
        return "Enter a positive price below R100,000,000."

    if price != price.quantize(Decimal("0.01")):
        return "Use no more than two decimal places for the price."

    if not availability or len(availability) > 200:
        return "Enter your availability using 1–200 characters."

    with transaction.atomic():
        job = ServiceRequest.objects.select_for_update().filter(
            pk=request_id
        ).first()

        if not job:
            return "That job number does not exist."

        invited = JobInvitation.objects.filter(
            service_request=job,
            provider=provider,
            sent_at__isnull=False,
        ).exists()

        if not invited:
            return "You have not been invited to quote on this job."

        if job.status not in ["open", "quoted"]:
            return "This job is no longer accepting quotes."

        quote, created = Quote.objects.get_or_create(
            service_request=job,
            provider=provider,
            defaults={
                "price": price,
                "availability": availability,
            },
        )

        if not created:
            return (
                f"You already submitted quote #{quote.pk} "
                f"for job #{job.pk}: R{quote.price:.2f}. "
                "Your existing quote has not been changed."
            )

        job.status = "quoted"
        job.save(update_fields=["status"])

    return (
        f"Quote #{quote.pk} saved for job #{job.pk}.\n"
        f"Price: R{quote.price:.2f}\n"
        f"Availability: {quote.availability}"
    )

def handle_provider_message(provider, incoming_message):
    text = incoming_message.strip()
    command = text.lower()

    with transaction.atomic():
        # Keep updates for this provider together.
        provider = Provider.objects.select_for_update().get(
            pk=provider.pk
        )

        conversation, _ = ProviderConversation.objects.get_or_create(
            provider=provider
        )

        if command == "cancel":
            conversation.delete()
            return "Draft quote cancelled. Send HI to view jobs."

        if command in ["hi", "hello", "start"]:
            jobs = list(
                ServiceRequest.objects.filter(
                    invitations__provider=provider,
                    invitations__sent_at__isnull=False,
                    status__in=["open", "quoted"],
                )
                .exclude(quotes__provider=provider)
                .distinct()
                .order_by("pk")[:10]
            )

            conversation.step = "job"
            conversation.job_options = [job.pk for job in jobs]
            conversation.service_request = None
            conversation.price = None
            conversation.availability = ""
            conversation.save()

            if not jobs:
                return "No invited jobs awaiting your quote. Send HI to check again."

            options = "\n".join(
                f"{number}. {job.get_service_type_display()} "
                f"in {job.location} — {job.description[:100]}"
                for number, job in enumerate(jobs, start=1)
            )

            return (
                "Which job would you like to quote on?\n\n"
                f"{options}\n\n"
                "Reply with its menu number.\n"
                "Send CANCEL to cancel."
            )

        if conversation.step == "job":
            valid_numbers = {
                str(number): job_id
                for number, job_id in enumerate(
                    conversation.job_options, start=1
                )
            }

            job_id = valid_numbers.get(text)

            if job_id is None:
                return "Choose a number from your job menu, or send HI to view it."

            job = ServiceRequest.objects.filter(
                pk=job_id,
                status__in=["open", "quoted"],
                invitations__provider=provider,
                invitations__sent_at__isnull=False,
            ).first()

            if not job:
                return "That job is no longer available. Send HI to refresh."

            conversation.service_request = job
            conversation.step = "price"
            conversation.save()

            return (
                f"Job: {job.get_service_type_display()} "
                f"in {job.location}\n"
                f"Problem: {job.description}\n\n"
                "What is your price in rand?\n"
                "Enter only the amount, for example: 550"
            )

        if conversation.step == "price":
            try:
                price = Decimal(text)
            except InvalidOperation:
                return "Enter only a numeric amount, for example: 550 or 550.50"

            if (
                not price.is_finite()
                or not Decimal("0") < price <= Decimal("99999999.99")
            ):
                return "Enter a positive price below R100,000,000."

            if price != price.quantize(Decimal("0.01")):
                return "Use no more than two decimal places."

            conversation.price = price
            conversation.step = "availability"
            conversation.save()

            return "When are you available? For example: Tomorrow at 9am."

        if conversation.step == "availability":
            if not text or len(text) > 200:
                return "Enter your availability using 1–200 characters."

            conversation.availability = text
            conversation.step = "confirm"
            conversation.save()

            return (
                "Please check your quote:\n\n"
                f"Job: #{conversation.service_request_id}\n"
                f"Price: R{conversation.price:.2f}\n"
                f"Available: {conversation.availability}\n\n"
                "1. Confirm\n"
                "2. Cancel\n\n"
                "Send START to begin again."
            )

        if conversation.step == "confirm":
            if text == "2":
                conversation.delete()
                return "Draft quote cancelled. Send HI to view jobs."

            if text != "1":
                return "Reply 1 to confirm or 2 to cancel."

            job = ServiceRequest.objects.select_for_update().filter(
                pk=conversation.service_request_id
            ).first()

            if not job or job.status not in ["open", "quoted"]:
                conversation.delete()
                return "This job is no longer accepting quotes. Send HI to view jobs."

            invited = JobInvitation.objects.filter(
                service_request=job,
                provider=provider,
                sent_at__isnull=False,
            ).exists()

            if not invited:
                conversation.delete()
                return "You are no longer invited to this job."

            quote, created = Quote.objects.get_or_create(
                service_request=job,
                provider=provider,
                defaults={
                    "price": conversation.price,
                    "availability": conversation.availability,
                },
            )

            if created:
                job.status = "quoted"
                job.save(update_fields=["status"])

            conversation.delete()

            return (
                f"Your quote for job #{job.pk} is saved.\n"
                f"Price: R{quote.price:.2f}\n"
                f"Available: {quote.availability}\n\n"
                "Send HI to view other jobs."
            )

        return "Send HI to view your invited jobs."

def send_quote_to_customer(quote):
    if quote.customer_notified_at:
        return "Customer notification already submitted."

    job = quote.service_request
    customer = job.customer

    client = Client(
        os.environ["TWILIO_ACCOUNT_SID"],
        os.environ["TWILIO_AUTH_TOKEN"],
    )

    message = client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"],
        to=f"whatsapp:{customer.phone_number}",
        body=(
            f"New quote for your FixBid request #{job.pk}\n\n"
            f"Service: {job.get_service_type_display()}\n"
            f"Area: {job.location}, {job.province}\n\n"
            f"Provider: {quote.provider.name}\n"
            f"Price: R{quote.price:.2f}\n"
            f"Available: {quote.availability}\n\n"
            "Quote selection will be available shortly."
        ),
    )

    quote.customer_notified_at = timezone.now()
    quote.save(update_fields=["customer_notified_at"])

    return message.sid