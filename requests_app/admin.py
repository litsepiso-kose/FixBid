from django.contrib import admin
from .models import Customer, Provider, ServiceRequest, Conversation, Quote, JobInvitation,ProviderConversation


admin.site.register(Customer)
admin.site.register(Provider)
admin.site.register(ServiceRequest)
admin.site.register(Conversation)
admin.site.register(Quote)
admin.site.register(JobInvitation)
admin.site.register(ProviderConversation)