import math


def calculate_obstacle_distances(
    current_x, current_y, obstacles, field_size, max_detect_distance
):
    num_sectors = 6
    sector_angle = 60
    distances = []
    field_width, field_height = field_size

    for i in range(num_sectors):
        angle = i * sector_angle
        rad = math.radians(angle)
        dx_dir = math.sin(rad)
        dy_dir = -math.cos(rad)

        min_distance = max_detect_distance

        # Расчет расстояния до границ поля
        tx = (
            (field_width - current_x) / dx_dir
            if dx_dir > 0
            else -current_x / dx_dir if dx_dir < 0 else float("inf")
        )
        ty = (
            (field_height - current_y) / dy_dir
            if dy_dir > 0
            else -current_y / dy_dir if dy_dir < 0 else float("inf")
        )

        t_field = max(0, min(tx, ty))
        if t_field < min_distance:
            min_distance = t_field

        # Проверка препятствий
        for obstacle in obstacles:
            obst_type, (cx, cy), sizes = obstacle

            if obst_type == "circle":
                radius = sizes[0]
                a = dx_dir**2 + dy_dir**2
                b = 2 * (dx_dir * (current_x - cx) + dy_dir * (current_y - cy))
                c = (current_x - cx) ** 2 + (current_y - cy) ** 2 - radius**2

                discriminant = b**2 - 4 * a * c
                if discriminant >= 0:
                    sqrt_discr = math.sqrt(discriminant)
                    t1 = (-b - sqrt_discr) / (2 * a)
                    t2 = (-b + sqrt_discr) / (2 * a)
                    for t in [t1, t2]:
                        if t > 0 and t < min_distance:
                            min_distance = t

            elif obst_type == "rectangle":
                w, h = sizes
                left = cx - w / 2
                right = cx + w / 2
                top = cy - h / 2
                bottom = cy + h / 2

                t_near = -float("inf")
                t_far = float("inf")

                if dx_dir != 0:
                    t1 = (left - current_x) / dx_dir
                    t2 = (right - current_x) / dx_dir
                    t_min, t_max = sorted([t1, t2])
                    t_near = max(t_near, t_min)
                    t_far = min(t_far, t_max)
                elif current_x < left or current_x > right:
                    continue

                if dy_dir != 0:
                    t1 = (top - current_y) / dy_dir
                    t2 = (bottom - current_y) / dy_dir
                    t_min, t_max = sorted([t1, t2])
                    t_near = max(t_near, t_min)
                    t_far = min(t_far, t_max)
                elif current_y < top or current_y > bottom:
                    continue

                if t_near > t_far or t_far < 0:
                    continue

                t_intersect = t_near if t_near > 0 else t_far
                if 0 < t_intersect < min_distance:
                    min_distance = t_intersect

        distances.append(min(min_distance, max_detect_distance))

    return distances