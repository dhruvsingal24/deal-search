from django.urls import path

from . import views, views_ui

urlpatterns = [
    # HTML frontend
    path("", views_ui.search_page, name="search-page"),
    # JSON API
    path("deals/search", views.DealSearchView.as_view(), name="deal-search"),
    path("deals", views.DealListCreateView.as_view(), name="deal-list-create"),
    path("searches", views.SearchHistoryView.as_view(), name="search-history"),
    path("cards", views.CardListView.as_view(), name="card-list"),
    path("health", views.HealthView.as_view(), name="health"),
    path("stats", views.StatsView.as_view(), name="stats"),
    path("cache", views.CacheView.as_view(), name="cache"),
]
