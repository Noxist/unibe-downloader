# Authentication Flow

The authentication relies on the national SWITCH edu-ID system utilizing SAML for Single Sign-On (SSO).

## Flow Steps

1.  **SWITCH edu-ID Login**: User enters credentials on the SWITCH login page.
2.  **Shibboleth SAML**: Successful login redirects via SAML to the university's Identity Provider (IdP).
3.  **ILIAS Session**: The IdP sets the final ILIAS session cookies.

## Required Cookies

To interact with ILIAS endpoints, the following cookies are essential:
*   `PHPSESSID`: Core ILIAS session token.
*   `_shibsession_*`: Shibboleth session tracker.
*   `ilClientId`: Must be set to `ilias3_unibe`.

### edu-ID Cookies
*   `__Host-JSESSIONID`
*   `shib_idp_session`
*   `ch_switch_aai_idp_otp_remember_browser`

> [!NOTE]
> The `_saml_idp` value generally decodes to: `aai-idp.unibe.ch/idp/shibboleth`

## Session Lifespan

Session cookies generally have `expires=-1` (session length) and require daily renewal by the automation script.

## Automation (`eduid_auth.py`)

The authentication process is fully automated:
1.  Enters Username.
2.  Enters Password.
3.  Generates and enters TOTP (using `pyotp`).
4.  Accepts any consent screens.

### Validation
A successful login is validated by checking for the presence of the logout glyph/button in the resulting HTML, *not* just relying on an HTTP 200 OK status, which could indicate a successful load of the login page itself.

> [!IMPORTANT]
> No passwords, TOTP secrets, or active session tokens should ever be stored in documentation or logged to standard output.
