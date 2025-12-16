# accounts/context_processors.py
def current_business(request):
    business = None
    if request.user.is_authenticated:
        # adjust to how you link a user to a business; examples:
        business = getattr(request.user, 'business', None)
        if not business:
            # if user can own multiple businesses, pick first (or None)
            qs = getattr(request.user, 'business_set', None)
            if qs:
                try:
                    business = qs.first()
                except Exception:
                    business = None

    return {'business': business}
