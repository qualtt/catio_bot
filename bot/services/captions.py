from bot.content import bot_content


def duplicate_note(duplicate_of_photo_id: int | None, duplicate_distance: int | None) -> str:
    if duplicate_of_photo_id is None:
        return ""

    if duplicate_distance == 0:
        return bot_content.message("duplicate_exact_note", photo_id=duplicate_of_photo_id)

    distance = duplicate_distance if duplicate_distance is not None else "unknown"
    return bot_content.message(
        "duplicate_similar_note",
        photo_id=duplicate_of_photo_id,
        distance=distance,
    )


def duplicate_short_note(duplicate_of_photo_id: int | None, duplicate_distance: int | None) -> str:
    if duplicate_of_photo_id is None:
        return ""

    if duplicate_distance == 0:
        return bot_content.message("duplicate_exact_short_note", photo_id=duplicate_of_photo_id)

    distance = duplicate_distance if duplicate_distance is not None else "unknown"
    return bot_content.message(
        "duplicate_similar_short_note",
        photo_id=duplicate_of_photo_id,
        distance=distance,
    )


def append_duplicate_note(text: str, duplicate_of_photo_id: int | None, duplicate_distance: int | None) -> str:
    return text + duplicate_note(duplicate_of_photo_id, duplicate_distance)


def submission_caption(
    *,
    animal_type: str | None,
    schedule: str,
    author: str,
    duplicate_of_photo_id: int | None = None,
    duplicate_distance: int | None = None,
) -> str:
    return bot_content.message(
        "admin_new_submission_caption",
        animal_type=animal_type,
        schedule=schedule,
        author=author,
        duplicate_note=duplicate_note(duplicate_of_photo_id, duplicate_distance),
    )


def album_submission_photo_caption(post, number: int) -> str:
    schedule = (
        post.schedule_time.strftime("%Y-%m-%d %H:%M")
        if post.schedule_time
        else bot_content.message("schedule_not_selected")
    )
    return bot_content.message(
        "admin_album_photo_caption",
        number=number,
        post_id=post.id,
        animal_type=post.animal_type,
        schedule=schedule,
        duplicate_note=duplicate_short_note(post.duplicate_of_photo_id, post.duplicate_distance),
    )


def admin_album_control_text(posts, *, author: str) -> str:
    ordered_posts = sorted(posts, key=lambda post: post.submission_group_index or post.id)
    lines = [
        bot_content.message(
            "admin_album_control_header",
            author=author,
            count=len(ordered_posts),
        ),
        bot_content.message("admin_album_duplicate_warning"),
        "",
    ]

    for index, post in enumerate(ordered_posts, start=1):
        schedule = (
            post.schedule_time.strftime("%Y-%m-%d %H:%M")
            if post.schedule_time
            else bot_content.message("schedule_not_selected")
        )
        lines.append(
            bot_content.message(
                "admin_album_control_line",
                number=post.submission_group_index or index,
                post_id=post.id,
                animal_type=post.animal_type,
                status=bot_content.status_label(post.status),
                schedule=schedule,
                duplicate_note=duplicate_short_note(post.duplicate_of_photo_id, post.duplicate_distance),
            )
        )

    return "\n".join(lines)
