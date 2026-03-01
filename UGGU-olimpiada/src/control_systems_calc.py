import math

CYBER_OBSTACLE = False

def update_speed_and_direction(current_pos, target_pos, current_speed, current_direction, obstacle_distances, max_speed):
    current_x, current_y = current_pos
    target_x, target_y = target_pos
    stop_radius = 5
    safe_distance = 20
    num_sectors = 6
    sector_angle = 60  # 360/360=1 градусов

    dx = target_x - current_x
    dy = target_y - current_y
    distance_to_target = math.hypot(dx, dy)

    if distance_to_target <= stop_radius:
        return 0, current_direction, "success"

    # Расчет направления к цели
    angle_to_target = math.degrees(math.atan2(dx, -dy)) % 360
    target_sector = int(angle_to_target / sector_angle) % num_sectors

    new_direction = angle_to_target
    new_speed = min(current_speed + 0.5, max_speed)

    # Объезд препятствий
    if len(obstacle_distances) > 0:
        if obstacle_distances[target_sector] < safe_distance:
            # Ищем лучший сектор из 6 возможных
            best_sector = max(range(num_sectors), key=lambda x: obstacle_distances[x])
            new_direction = best_sector * sector_angle

    if CYBER_OBSTACLE:
        new_direction -= 60

    # Торможение у цели
    braking_distance = new_speed * 2
    if distance_to_target < braking_distance:
        new_speed = max(0, min(new_speed, distance_to_target / 2))

    return new_speed, round(new_direction, 0), "moved"
