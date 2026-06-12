




from django.urls import path
from django.shortcuts import render
from archive import insight, itemfolder
from . import metada, views, usermanagement,views_signature

app_name = 'archive'

urlpatterns = [
    # ---- Core views ----
    # path('api/folders/<int:folder_id>/soft-delete/', itemfolder.soft_delete_item_folder, name='soft_delete_folder'),
    # path('api/files/<uuid:asset_uuid>/soft-delete/', itemfolder.soft_delete_item_file, name='soft_delete_file'),
    path('home/', views.home, name='home'),
    path('collections/', views.collections_view, name='collections'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('register/', views.register_view, name='register'),
    path('document/<int:item_id>/', views.document_detail, name='document_detail'),
    path('download/<uuid:asset_uuid>/', views.download_file, name='download_file'),

    # ---- API endpoints ----
    path('api/archive-items/', views.api_archive_items, name='api_archive_items'),
    path('api/archive-items/<int:item_id>/', views.api_archive_item_detail, name='api_archive_item_detail'),
    path('api/archive-items/<int:item_id>/update/', views.api_update_archive_item, name='api_update_archive_item'),
    path('api/customer-autocomplete/', views.customer_autocomplete, name='customer_autocomplete'),
    path('customer/<str:customer_id>/', views.customer_detail, name='customer_detail'),

    path('api/items/<int:item_id>/metadata/', metada.api_metadata_list, name='api_metadata_list'),
    path('api/items/<int:item_id>/metadata/upsert/', metada.api_metadata_upsert, name='api_metadata_upsert'),
    path('api/items/<int:item_id>/metadata/<int:metadata_id>/delete/', metada.api_metadata_delete, name='api_metadata_delete'),
    path('api/items/<int:item_id>/relations/', views.get_related_items, name='item_relations'),
    path('api/items/<int:item_id>/graph/', views.get_relationship_graph, name='item_graph'),
    path('api/items/<int:item_id>/update_full/', itemfolder.update_document_full, name='update_document_full'),
    path('api/items/<int:item_id>/update_tags/', itemfolder.update_item_tags, name='update_item_tags'),

    # ---- Folders (item folders) ----
    path('api/items/<int:item_id>/folders/', itemfolder.item_folders_api, name='item_folders_api'),
    path('api/folders/<int:folder_id>/delete/', itemfolder.delete_item_folder, name='delete_item_folder'),
    path('api/folders/<int:folder_id>/add-file/', itemfolder.add_file_to_folder, name='add_file_to_folder'),
    path('api/folder-files/<uuid:asset_uuid>/delete/', itemfolder.delete_item_file, name='delete_item_file'),

    # ---- Document folders (non-loan) ----
    path('api/document-folders/<int:item_id>/', itemfolder.document_folders_api, name='document_folders_api'),
    path('api/document-folders/<int:folder_id>/delete/', itemfolder.delete_document_folder, name='delete_document_folder'),
    path('api/document-folders/<int:folder_id>/add-file/', itemfolder.add_file_to_document_folder, name='add_file_to_document_folder'),
    path('api/document-files/<int:file_id>/delete/', itemfolder.delete_document_file, name='delete_document_file'),
    path('api/document-files/<uuid:asset_uuid>/delete/', itemfolder.delete_document_file, name='delete_document_file'),

    # ---- Shared folder API (for AJAX) ----
    path('api/search-files/', itemfolder.search_files_api, name='search_files_api'),
    path('api/shared-folders/<int:pk>/add-file/', itemfolder.add_file_to_shared_folder, name='api_add_file_to_shared_folder'),
    path('api/shared-folders/<int:pk>/remove-file/<int:file_pk>/', itemfolder.remove_file_from_shared_folder, name='api_remove_file_from_shared_folder'),
    path('api/shared-folders/<int:pk>/access/', itemfolder.shared_folder_access_api, name='api_shared_folder_access'),
    path('api/shared-folders/<int:pk>/revoke-access/', itemfolder.revoke_shared_folder_access, name='api_revoke_shared_folder_access'),
    path('api/shared-folders/<int:pk>/update/', itemfolder.shared_folder_update_api, name='api_shared_folder_update'),



    path('deleted-items/', itemfolder.deleted_items_list, name='deleted_items'),
    path('files/<int:file_id>/restore/', itemfolder.restore_item_file, name='restore_file'),
    path('files/<int:file_id>/permanent-delete/', itemfolder.permanent_delete_item_file, name='permanent_delete_file'),
    # path('folders/<int:folder_id>/soft-delete/', itemfolder.soft_delete_item_folder, name='soft_delete_folder'),
    path('folders/<int:folder_id>/restore/', itemfolder.restore_item_folder, name='restore_folder'),
    path('folders/<int:folder_id>/permanent-delete/', itemfolder.permanent_delete_item_folder, name='permanent_delete_folder'),
    # path('files/<uuid:asset_uuid>/soft-delete/', itemfolder.soft_delete_item_file, name='soft_delete_file'),



    # path('api/folders/<int:folder_id>/soft-delete/', itemfolder.soft_delete_item_folder, name='soft_delete_folder'),
    # path('api/files/<uuid:asset_uuid>/soft-delete/', itemfolder.soft_delete_item_file, name='soft_delete_file'),
    # path('delete-file/<uuid:asset_uuid>/', itemfolder.soft_delete_item_file, name='soft_delete_file'),


    # ---- Shared folder HTML (non-API) ----
    path('shared-folders/', itemfolder.shared_folders_manage, name='shared_folders_manage'),
    path('shared-folders/<int:pk>/edit/', itemfolder.shared_folder_edit, name='shared_folder_edit'),
    path('shared-folders/<int:pk>/view/', itemfolder.view_shared_folder, name='view_shared_folder'),
    path('my-shared-folders/', itemfolder.my_shared_folders, name='my_shared_folders'),

    # ---- Access requests ----
    path('access/request/<int:item_id>/', views.request_access, name='request_access'),
    path('access/request-document/<int:item_id>/', itemfolder.request_document_access, name='request_document_access'),
    path('access/my-requests/', views.my_access_requests, name='my_access_requests'),
    path('access/pending/', views.pending_access_requests, name='pending_access_requests'),
    path('access/approve/<int:request_id>/', views.approve_access_request, name='approve_access_request'),
    path('access/deny/<int:request_id>/', views.deny_access_request, name='deny_access_request'),

    # ---- User management ----
    path('staff/users/', usermanagement.manage_users, name='manage_users'),
    path('staff/users/<int:user_id>/assign/', usermanagement.assign_role, name='assign_role'),
    path('staff/create-user/', usermanagement.admin_create_user, name='admin_create_user'),
    path('staff/users/<int:user_id>/edit/', usermanagement.edit_user, name='edit_user'),
    path('staff/users/<int:user_id>/toggle-active/', usermanagement.toggle_user_active, name='toggle_user_active'),
    path('staff/users/<int:user_id>/reset-password/', usermanagement.reset_user_password, name='reset_user_password'),
    path('staff/users/<int:user_id>/force-logout/', usermanagement.force_logout_user, name='force_logout_user'),
    path('change-password/', usermanagement.change_password, name='change_password'),
    path('force-change-password/', usermanagement.force_change_password, name='force_change_password'),
    path('extend-session/', usermanagement.extend_session, name='extend_session'),

    # ---- Collections, insights, audit ----
    path('collections/<int:pk>/edit/', views.edit_collection, name='edit_collection'),
    path('collections/<int:pk>/delete/', views.delete_collection, name='delete_collection'),
    path('audit-logs/', views.audit_logs_view, name='audit_logs'),
    path('insights/', insight.insights_dashboard, name='insights'),

    # ---- User folders (non-shared) ----
    path('my-folders/', views.my_folders, name='my_folders'),
    path('folders/create/', views.create_folder, name='create_folder'),
    path('assign-to-folder/<int:item_id>/', views.assign_to_folder, name='assign_to_folder'),

    # ---- Folder types ----
    path('folder-types/', itemfolder.folder_type_manage, name='folder_type_list'),


    path('api/items/<int:item_id>/log-view/', views.log_item_view, name='log_item_view'),

    # path('/files/<uuid:asset_uuid>/soft-delete/', itemfolder.soft_delete_item_file, name='soft_delete_file'),

    #################################################################################################################
    path('signature/dashboard/', views_signature.signature_dashboard, name='signature_dashboard'),
    path('signature/pending/', views_signature.pending_signatures, name='pending_signatures'),
    path('signature/sent/', views_signature.sent_envelopes, name='sent_envelopes'),
    path('signature/completed/', views_signature.completed_envelopes, name='completed_envelopes'),
    path('signature/envelope/<int:envelope_id>/', views_signature.envelope_detail, name='envelope_detail'),
    path('signature/envelope/<int:envelope_id>/void/', views_signature.void_envelope, name='void_envelope'),  # ADD THIS LINE
    path('signature/create/<int:file_id>/<str:file_type>/', views_signature.create_signature_envelope, name='create_signature_envelope'),
    path('sign/portal/<uuid:token>/', views_signature.sign_portal, name='sign_portal'),
    path('signature/complete/', views_signature.signature_complete, name='signature_complete'),
    path('signature/declined/', views_signature.signature_declined, name='signature_declined'),
    
    # Loan signature URLs
    path('loan-signature/requests/', views_signature.loan_signature_requests, name='loan_signature_requests'),
    path('loan-signature/pending/', views_signature.loan_signature_pending, name='loan_signature_pending'),
    path('loan-signature/history/', views_signature.loan_signature_history, name='loan_signature_history'),
    path('api/loan-documents/<int:loan_id>/', views_signature.get_loan_documents_api, name='get_loan_documents'),
    path('api/create-loan-signature/', views_signature.create_loan_signature_envelope, name='create_loan_signature_envelope'),
 
    path('serve-file/<path:file_path>/', views_signature.serve_file, name='serve_file'),
    path('sign/save-signature/<uuid:token>/', views_signature.save_signature, name='save_signature'),


    path('signature/envelope/<int:envelope_id>/add-recipient/', views_signature.add_recipient_to_envelope, name='add_recipient_to_envelope'),
    path('signature/api/search-users/', views_signature.search_users, name='search_users'),
    path('signature/recipient/<int:recipient_id>/remove/', views_signature.remove_recipient, name='remove_recipient'),
    

    #################################################################################################################



    path('api/request-delete/', itemfolder.request_delete_item, name='request_delete_item'),
    # path('api/admin/approve-deletion/<int:request_id>/', itemfolder.approve_deletion, name='approve_deletion'),
    # path('api/admin/reject-deletion/<int:request_id>/', itemfolder.reject_deletion, name='reject_deletion'),
    # path('admin/pending-deletions/', itemfolder.pending_deletions_list, name='pending_deletions'),


    path('pending-deletions/', itemfolder.pending_deletions_list, name='pending_deletions'),
    path('api/approve-deletion/<int:request_id>/', itemfolder.approve_deletion, name='approve_deletion'),
    path('api/reject-deletion/<int:request_id>/', itemfolder.reject_deletion, name='reject_deletion'),
    path('folders/<int:folder_id>/deleted-files/', itemfolder.deleted_folder_files, name='deleted_folder_files'),
    path('folders/<int:folder_id>/files/', itemfolder.folder_files, name='folder_files'),

]