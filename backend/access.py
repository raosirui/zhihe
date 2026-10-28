"""One visibility/learning/download policy for JSON, images and resources."""


def is_manager(toolkit, user):
    return bool(user and (user.role == "admin" or toolkit.author_id == user.id))


def is_visible(toolkit, user, purchased=False):
    if is_manager(toolkit, user):
        return True
    return toolkit.status == "published" or (toolkit.status == "offline" and bool(purchased))


def can_learn(toolkit, user, purchased=False, lesson=None):
    if is_manager(toolkit, user):
        return True
    if toolkit.status == "offline":
        return bool(purchased)
    if toolkit.status != "published":
        return False
    if toolkit.price == 0 or purchased:
        return True
    return bool(lesson and lesson.is_preview)


def can_download(toolkit, user, purchased=False):
    return bool(user and can_learn(toolkit, user, purchased))
