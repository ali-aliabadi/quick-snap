from django.urls import path

from . import views

app_name = "snap"

urlpatterns = [
    path("<slug:slug>/", views.join, name="join"),
    path("<slug:slug>/camera/", views.camera, name="camera"),
    path("<slug:slug>/capture/", views.capture, name="capture"),
    path("<slug:slug>/done/", views.done, name="done"),
]
