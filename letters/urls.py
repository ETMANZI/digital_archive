from django.urls import path
from . import views

app_name = 'letters'

urlpatterns = [
    path('create/', views.create_letter, name='create_letter'),
    path('my-letters/', views.my_letters, name='my_letters'),
    path('manage/<int:letter_id>/', views.manage_letter, name='manage_letter'),
    path('sign/<uuid:token>/', views.sign_letter, name='sign_letter'),
    path('api/add-field/', views.add_signature_field, name='add_field'),
    path('api/add-recipient/<int:letter_id>/', views.add_recipient, name='add_recipient'),
    path('api/remove-recipient/<int:request_id>/', views.remove_recipient, name='remove_recipient'),
    path('api/submit-signatures/<uuid:token>/', views.submit_signatures, name='submit_signatures'),
    path('api/search-users/', views.search_users, name='search_users'),
]