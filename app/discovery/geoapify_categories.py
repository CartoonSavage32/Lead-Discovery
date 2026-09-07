from __future__ import annotations

from app.models import Industry

# Geoapify Places categories for each pipeline industry. Extend here when a
# live search returns the wrong kind of POI — do not widen scoring/filters.
DEFAULT_GEOAPIFY_CATEGORIES: dict[str, list[str]] = {
    "bakery": ["commercial.food_and_drink.bakery"],
    "restaurant": ["catering.restaurant"],
    "cafe": ["catering.cafe"],
    "dentist": ["healthcare.dentist"],
    "lawyer": ["office.lawyer"],
    # Geoapify has no plumber Places category; plumbing suppliers are the closest supported set.
    "plumber": ["commercial.houseware_and_hardware"],
    "electrician": ["service.electrician"],
    "hairdresser": ["service.beauty.hairdresser"],
    "hotel": ["accommodation.hotel"],
    "gym": ["sport.fitness.gym", "sport.fitness.fitness_centre"],
    "pharmacy": ["healthcare.pharmacy", "commercial.health_and_beauty.pharmacy"],
    "real estate": ["office.estate_agent", "service.estate_agent"],
    "accountant": ["office.accountant"],
    "veterinarian": ["pet.veterinary"],
    "auto repair": ["service.vehicle.repair.car", "service.vehicle.repair"],
    "florist": ["commercial.florist"],
}


def categories_for_industry(industry: Industry) -> list[str]:
    if industry.geoapify_categories:
        return list(industry.geoapify_categories)
    return list(DEFAULT_GEOAPIFY_CATEGORIES.get(industry.name, []))
