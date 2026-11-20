from django.urls import path

from . import views

urlpatterns = [
    path("", views.create_ticket, name="home"),
    path("tickets/new/", views.create_ticket, name="create_ticket"),
    path("tickets/<int:ticket_id>/", views.ticket_detail, name="ticket_detail"),
    path("staff/queue/", views.staff_dashboard, name="staff_dashboard"),
    path("staff/tickets/", views.my_tickets, name="my_tickets"),
    path(
        "staff/tickets/<int:ticket_id>/claim/",
        views.claim_ticket,
        name="claim_ticket",
    ),
    path("faq/answer/", views.get_faq_answer, name="get_faq_answer"),
]
