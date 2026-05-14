from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView
from django.conf import settings
from django.contrib.staticfiles.urls import staticfiles_urlpatterns

urlpatterns = [
    path("", RedirectView.as_view(url="/paper-trading/", permanent=False)),
    path("admin/", admin.site.urls),
    path("paper-trading/", include("paper_trading.urls")),
]

if settings.DEBUG:
    urlpatterns += staticfiles_urlpatterns()
