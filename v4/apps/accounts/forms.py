from django import forms


class LoginForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs={"autofocus": True,
                                                                          "autocomplete": "username"}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    auth_method = forms.ChoiceField(choices=[("auto", "auto"), ("local", "local"), ("ldap", "ldap")],
                                    required=False, initial="local")


class PasswordChangeForm(forms.Form):
    current_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    new_password = forms.CharField(
        min_length=4, widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    confirm_password = forms.CharField(
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def clean(self):
        data = super().clean()
        if data.get("new_password") and data.get("new_password") != data.get("confirm_password"):
            self.add_error("confirm_password", "The new passwords do not match.")
        return data
