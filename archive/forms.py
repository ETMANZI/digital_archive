# forms.py
from django import forms
from .models import AccessRequest, Collection, SharedFolder
from django.contrib.auth.forms import UserCreationForm
from .models import User
from django.contrib.auth.models import Group
from django.utils import timezone

class CollectionForm(forms.ModelForm):
    class Meta:
        model = Collection
        fields = ['name', 'description', 'access_policy']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Collection name'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Optional description'}),
            'access_policy': forms.Select(attrs={'class': 'form-control'}),
        }


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    department = forms.CharField(max_length=100, required=False)
    
    class Meta:
        model = User
        fields = ('username', 'email', 'department', 'password1', 'password2')
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        user.department = self.cleaned_data['department']
        if commit:
            user.save()
        return user
    

    
class AdminUserCreationForm(UserCreationForm):
    first_name = forms.CharField(max_length=150, required=True, label="First name")
    last_name = forms.CharField(max_length=150, required=True, label="Last name")
    email = forms.EmailField(required=True)
    
    DEPARTMENT_CHOICES = [
        ('', ''),
        ('HR', 'Human Resources'),
        ('IT', 'Information Technology'),
        ('FINANCE', 'Finance'),
        ('AUDIT', 'Audit'),
        ('OPERATIONS', 'Operations'),
        ('LEGAL', 'Legal'),
        ('RISK', 'Risk Management'),
        ('CREDIT', 'Credit Management'),
        ('COMPLAINCE', 'Compliance'),
        ('INTERNAL CONTROL', 'Internal Control'),
    ]
    department = forms.ChoiceField(
        choices=DEPARTMENT_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Select roles for this user"
    )

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'email', 'department', 'groups', 'password1', 'password2')

    def save(self, commit=True):
        user = super().save(commit=False)
        user.first_name = self.cleaned_data['first_name']
        user.last_name = self.cleaned_data['last_name']
        user.email = self.cleaned_data['email']
        user.department = self.cleaned_data['department']  
        if commit:
            user.save()
        if commit:
            user.groups.set(self.cleaned_data['groups'])
        return user

class AccessRequestForm(forms.ModelForm):
    class Meta:
        model = AccessRequest
        fields = ['reason', 'granted_access_until']
        widgets = {
            'granted_access_until': forms.DateTimeInput(attrs={'type': 'datetime-local'}),
            'reason': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Explain why you need access...'}),
        }
        help_texts = {
            'granted_access_until': 'When should the access expire? (max 30 days from now)'
        }
    
    def clean_granted_access_until(self):
        dt = self.cleaned_data['granted_access_until']
        if dt < timezone.now():
            raise forms.ValidationError('Expiration date must be in the future.')
        # optional: limit to 30 days
        if dt > timezone.now() + timezone.timedelta(days=30):
            raise forms.ValidationError('Access cannot be requested for more than 30 days.')
        return dt
    


class SharedFolderForm(forms.ModelForm):
    class Meta:
        model = SharedFolder
        fields = ['name', 'description']