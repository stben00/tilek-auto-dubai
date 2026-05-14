from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

EDIT_FIELDS = [
    ("title", "Title"),
    ("brand", "Brand"),
    ("model", "Model"),
    ("year", "Year"),
    ("engine", "Engine"),
    ("fuel", "Fuel"),
    ("bodyType", "Body Type"),
    ("price", "Price"),
    ("mileage", "Mileage"),
    ("location", "Location"),
    ("description", "Description"),
    ("whatsapp", "WhatsApp"),
    ("instagramUrl", "Instagram URL"),
    ("videoUrl", "Video URL"),
    ("status", "Status"),
]


def main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Publish", callback_data="publish"),
         InlineKeyboardButton(text="✏️ Edit", callback_data="edit")],
        [InlineKeyboardButton(text="🔁 Regenerate text", callback_data="regen_text"),
         InlineKeyboardButton(text="🎨 Regenerate poster", callback_data="regen_image")],
        [InlineKeyboardButton(text="🖼 Add Photos", callback_data="add_photos"),
         InlineKeyboardButton(text="🎥 Add Video", callback_data="add_video")],
        [InlineKeyboardButton(text="🔗 Add Instagram Link", callback_data="add_insta"),
         InlineKeyboardButton(text="👁 Preview", callback_data="preview")],
        [InlineKeyboardButton(text="🧹 Clear Draft", callback_data="clear"),
         InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")],
    ])


def edit_kb() -> InlineKeyboardMarkup:
    rows = []
    row = []
    for key, label in EDIT_FIELDS:
        row.append(InlineKeyboardButton(text=label, callback_data=f"editf:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Back", callback_data="back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Yes, publish", callback_data="confirm_publish"),
         InlineKeyboardButton(text="⬅️ Back", callback_data="back")],
    ])
