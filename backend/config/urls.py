from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', include('sender.urls')),
]

# MEDIA_ROOT is deliberately NOT exposed here.
#
# It used to be, via static(), which made every uploaded campaign photo and
# video world-readable at a guessable URL with no authentication — anyone who
# learned a filename could pull the file. Campaign media is now streamed
# through sender.views.serve_media_file, which checks that the requesting
# token belongs to the campaign's owner.
#
# static() only ever served files in DEBUG anyway, so production was never
# affected — but the exposure was real for anyone running locally or on a
# LAN, which is exactly where you'd test with real customer data.

if settings.DEBUG:
    # Only admin-uploaded assets need static serving, and those live in
    # STATIC_ROOT, not MEDIA_ROOT.
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / 'staticfiles')
