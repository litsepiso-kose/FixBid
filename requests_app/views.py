import os
from .models import Customer, ServiceRequest, SERVICE_CHOICES, Conversation, Provider
from django.shortcuts import render
from django.db import transaction

from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse
from .services import handle_provider_message

SERVICES = {
    "1": "plumbing",
    "2": "electrical",
    "3": "painting",
    "4": "cleaning",
    "5": "carpentry",
}

PROVINCES = {
    "1": "Eastern Cape",
    "2": "Free State",
    "3": "Gauteng",
    "4": "KwaZulu-Natal",
    "5": "Limpopo",
    "6": "Mpumalanga",
    "7": "North West",
    "8": "Northern Cape",
    "9": "Western Cape",
}

SERVICE_MENU = (
    "Welcome to FixBid!\nWhich service do you need?\n\n"
    + "\n".join(
        f"{number}. {service.title()}"
        for number, service in SERVICES.items()
    )
    + "\n\nReply with a number. Send START to restart."
)

PROVINCE_MENU = (
    "Which province?\n\n"
    + "\n".join(
        f"{number}. {province}"
        for number, province in PROVINCES.items()
    )
    + "\n\nReply with a number."
)

@csrf_exempt
@require_POST
def whatsapp_webhook(request):
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    webhook_url = os.environ.get("TWILIO_WEBHOOK_URL")

    if not auth_token or not webhook_url:
        return HttpResponse("WhatsApp configuration missing", status=503)

    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")

    if not validator.validate(webhook_url, request.POST, signature):
        return HttpResponse("Invalid signature", status=403)

    incoming_message = request.POST.get("Body", "").strip()
    sender = request.POST.get("From", "")
    message_sid = request.POST.get("MessageSid", "")

    if not sender.startswith("whatsapp:") or not message_sid:
        return HttpResponse("Invalid message", status=400)

    phone_number = sender.removeprefix("whatsapp:")

    provider = Provider.objects.filter(
      phone_number=phone_number
    ).first()

    if provider:
        reply = handle_provider_message(provider, incoming_message)

        response = MessagingResponse()
        response.message(reply)

        return HttpResponse(
            str(response),
            content_type="application/xml",
        )

    customer, _ = Customer.objects.get_or_create(
        phone_number=phone_number,
        defaults={
            "name": request.POST.get("ProfileName") or "WhatsApp Customer"
        },
    )

    response = MessagingResponse()

    with transaction.atomic():
        conversation, _ = Conversation.objects.get_or_create(
            customer=customer,
        )

        # Lock this customer's conversation while updating it.
        conversation = Conversation.objects.select_for_update().get(
            pk=conversation.pk
        )

        existing_request = ServiceRequest.objects.filter(
            source_message_sid=message_sid
        ).first()

        if existing_request:
            reply = (
                f"Your request #{existing_request.pk} "
                "has already been saved."
            )

        elif incoming_message.lower() in ["hi", "hello", "start"]:
            conversation.step = "service"
            conversation.service_type = ""
            conversation.province = ""
            conversation.suburb = ""
            conversation.description = ""
            conversation.media_url = ""
            conversation.media_type = ""
            conversation.save()
            reply = SERVICE_MENU

        elif incoming_message.lower() == "cancel":
            conversation.delete()
            reply = "Request cancelled. Send HI to start again."

        elif conversation.step == "service":
            service = SERVICES.get(incoming_message)

            if service:
                conversation.service_type = service
                conversation.step = "province"
                conversation.save()
                reply = PROVINCE_MENU
            else:
                reply = "Please reply with a service number from 1 to 5."

        elif conversation.step == "province":
            province = PROVINCES.get(incoming_message)

            if province:
                conversation.province = province
                conversation.step = "suburb"
                conversation.save()
                reply = "Type your suburb or town, for example: Ficksburg."
            else:
                reply = "Please reply with a province number from 1 to 9."

        elif conversation.step == "suburb":
            if not incoming_message or len(incoming_message) > 100:
                reply = "Please enter a suburb or town using 1–100 characters."
            else:
                conversation.suburb = incoming_message
                conversation.step = "details"
                conversation.save()
                reply = (
                    "Describe the problem.\n\n"
                    "You can also send one photo or video, "
                    "with a caption or as a separate message.\n\n"
                    "When finished, send DONE.\n"
                    "Send CANCEL to cancel."
                )

        elif conversation.step == "details":
            if incoming_message.lower() == "done":
                if not conversation.description:
                    reply = "Please describe the problem before sending DONE."
                else:
                    conversation.step = "confirm"
                    conversation.save()

                    media_label = (
                        "Attached" if conversation.media_url else "None"
                    )

                    reply = (
                        "Please check your request:\n\n"
                        f"Service: {conversation.service_type.title()}\n"
                        f"Province: {conversation.province}\n"
                        f"Suburb/town: {conversation.suburb}\n"
                        f"Problem: {conversation.description}\n"
                        f"Photo/video: {media_label}\n\n"
                        "1. Confirm\n"
                        "2. Cancel\n\n"
                        "Reply 1 or 2. Send START to restart."
                    )
            else:
                media_url = request.POST.get("MediaUrl0", "")
                media_type = request.POST.get("MediaContentType0", "")

                if media_url and not media_type.startswith(
                    ("image/", "video/")
                ):
                    reply = "Please send a photo or video, rather than another file type."
                elif (
                    media_url
                    and conversation.media_url
                    and media_url != conversation.media_url
                ):
                    reply = (
                        "This version accepts one photo or video. "
                        "Your first attachment is saved. "
                        "Send DONE when finished."
                    )
                else:
                    if incoming_message:
                        # Avoid adding the same text twice on a retry.
                        lines = conversation.description.split("\n")

                        if incoming_message not in lines:
                            conversation.description = "\n".join(
                                filter(
                                    None,
                                    [
                                        conversation.description,
                                        incoming_message,
                                    ],
                                )
                            )

                    if media_url:
                        conversation.media_url = media_url
                        conversation.media_type = media_type

                    conversation.save()
                    reply = (
                        "Details received. You may add a description "
                        "or one photo/video. Send DONE when finished."
                    )

        elif conversation.step == "confirm":
            if incoming_message == "1":
                service_request = ServiceRequest.objects.create(
                    customer=customer,
                    service_type=conversation.service_type,
                    province=conversation.province,
                    location=conversation.suburb,
                    description=conversation.description,
                    media_url=conversation.media_url,
                    media_type=conversation.media_type,
                    source_message_sid=message_sid,
                )

                conversation.delete()

                reply = (
                    f"Your request #{service_request.pk} has been saved!\n"
                    "Send HI to make another request."
                )

            elif incoming_message == "2":
                conversation.delete()
                reply = "Request cancelled. Send HI to start again."
            else:
                reply = "Reply 1 to confirm or 2 to cancel."

        else:
            reply = "Send HI to start a service request."

    response.message(reply)

    return HttpResponse(
        str(response),
        content_type="application/xml",
    )

