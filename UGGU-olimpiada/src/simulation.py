from flask import Flask, jsonify, request, render_template_string
import math
import time

app = Flask(__name__)

# Конфигурация шахты и препятствий
CONFIG = {
    "field_width": 800,
    "field_height": 600,
    "obstacles": [
        ["rectangle", (0, 50), (300, 30)],
        ["circle", (325, 150), (45,)],
        ["rectangle", (200, 120), (30, 150)],
        ["rectangle", (425, 0), (30, 400)],
        ["rectangle", (225, 300), (30, 250)],
        ["rectangle", (0, 300), (225, 30)],
        ["rectangle", (320, 400), (200, 30)],
        ["rectangle", (320, 490), (200, 30)],
        ["rectangle", (320, 570), (200, 30)],
        ["circle", (600, 470), (50,)],
        ["circle", (650, 370), (50,)],
        ["circle", (550, 270), (60,)],
        ["circle", (700, 170), (70,)],
        ["circle", (780, 25), (70,)],
        ["circle", (780, 575), (70,)],
        ["circle", (50, 300), (70,)],
    ],
    "start_position": (20, 20),
    "end_position": (691, 68),
}

# Глобальное состояние
state = {
    "x": CONFIG["start_position"][0],
    "y": CONFIG["start_position"][1],
    "speed": 0.0,
    "direction": 0.0,
    "points": [],
    "trail": [],
    "last_update": time.time(),
}


def is_collision(x, y):
    """Проверка столкновения с препятствиями"""
    for obs in CONFIG["obstacles"]:
        obs_type, pos, params = obs
        cx, cy = pos

        if obs_type == "circle":
            radius = params[0]
            if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                return True

        elif obs_type == "rectangle":
            w, h = params
            if cx <= x <= cx + w and cy <= y <= cy + h:
                return True

    return False


def update_position():
    """Обновление позиции с учетом направлений света"""
    now = time.time()
    delta = now - state["last_update"]

    if state["speed"] > 0:
        state["trail"].append((state["x"], state["y"]))

        # Преобразование в математические координаты
        math_angle = math.radians(90 - state["direction"])
        distance = state["speed"] * delta

        step_size = 2
        steps = max(1, int(distance // step_size))
        dx_step = distance * math.cos(math_angle) / steps
        dy_step = -distance * math.sin(math_angle) / steps

        for _ in range(steps):
            new_x = state["x"] + dx_step
            new_y = state["y"] + dy_step

            new_x = max(0, min(new_x, CONFIG["field_width"]))
            new_y = max(0, min(new_y, CONFIG["field_height"]))

            if is_collision(new_x, new_y):
                state["speed"] = 0.0
                break

            state["x"] = new_x
            state["y"] = new_y

    state["last_update"] = now


@app.route("/config")
def get_config():
    return jsonify(CONFIG)


@app.route("/status")
def get_status():
    update_position()
    # time.sleep(0.4)
    return jsonify(
        {
            "x": round(state["x"], 0),
            "y": round(state["y"], 0),
            "speed": state["speed"],
            "direction": state["direction"],
            "points": state["points"],
            "trail": state["trail"],
        }
    )


@app.route("/position")
def get_position():
    update_position()
    return jsonify(
        {
            "x": round(state["x"], 0),
            "y": round(state["y"], 0),
        }
    )


@app.route("/set_velocity", methods=["POST"])
def set_velocity():
    data = request.json
    state["speed"] = float(data.get("speed", 0))
    state["direction"] = float(data.get("direction", 0))
    state["last_update"] = time.time()
    return jsonify({"status": "success"})


@app.route("/add_point/<point_type>", methods=["POST"])
def add_point(point_type):
    if point_type not in ["waypoint", "checkpoint"]:
        return jsonify({"error": "Invalid point type"}), 400

    data = request.json
    state["points"].append(
        {
            "type": point_type,
            "x": float(data["x"]),
            "y": float(data["y"]),
            "timestamp": time.time(),
        }
    )
    return jsonify({"status": "success"})


@app.route("/clear_points", methods=["POST"])
def clear_points():
    state["points"].clear()
    return jsonify({"status": "success"})


@app.route("/reset_position", methods=["POST"])
def reset_position():
    state["x"] = CONFIG["start_position"][0]
    state["y"] = CONFIG["start_position"][1]
    state["speed"] = 0.0
    state["direction"] = 0.0
    state["trail"] = []
    return jsonify({"status": "success"})


@app.route("/load_points", methods=["POST"])
def load_points():
    data = request.json
    if not isinstance(data, list):
        return jsonify({"error": "Invalid format"}), 400

    state["points"] = []
    for item in data:
        if "type" in item and "x" in item and "y" in item:
            state["points"].append(
                {
                    "type": item["type"],
                    "x": float(item["x"]),
                    "y": float(item["y"]),
                    "timestamp": time.time(),
                }
            )
    return jsonify({"status": "success"})


@app.route("/")
def index():
    return render_template_string(
        """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Управление АРПБТ</title>
        <style>
            body { margin: 0; font-family: Arial; }
            canvas { border: 1px solid #000; }
            .controls { padding: 10px; background: #f0f0f0; }
            button { padding: 8px 15px; margin: 5px; cursor: pointer; }
            .info-panel {
                position: fixed; 
                top: 10px; 
                right: 10px; 
                background: rgba(255,255,255,0.9);
                padding: 15px;
                border: 1px solid #000;
                border-radius: 5px;
            }
            input[type="number"] { width: 70px; padding: 3px; }
        </style>
    </head>
    <body>
        <div class="controls">
            <button onclick="setMode('waypoint')">Точка маршрута</button>
            <button onclick="setMode('checkpoint')">Контрольная точка</button>
            <button onclick="copyPoints()">Копировать точки</button>
            <button onclick="clearPoints()">Очистить точки</button>
            <button onclick="resetPosition()">Сбросить позицию</button>
            <!--<div style="margin-top:10px;">
                Скорость: <input type="number" id="speed" step="0.1">
                Направление: <input type="number" id="direction" step="1">
                <button onclick="updateVelocity()">Применить</button>
            </div>-->
            <textarea id="pointsData" placeholder="Введите JSON с точками"></textarea>
            <button onclick="loadPoints()">Загрузить точки</button>
        </div>
        
        <div class="info-panel">
            <h3>Состояние АРПБТ:</h3>
            <div>Позиция: <span id="posX">-</span>, <span id="posY">-</span></div>
            <div>Скорость: <span id="speedVal">0</span> px/s</div>
            <div>Направление: <span id="directionVal">0</span>°</div>
        </div>
        
        <canvas id="field"></canvas>

        <script>
            let canvas, ctx;
            let currentMode = 'waypoint';
            let fieldConfig = null;

            window.onload = function() {
                canvas = document.getElementById('field');
                ctx = canvas.getContext('2d');
                
                canvas.addEventListener('click', event => {
                    const rect = canvas.getBoundingClientRect();
                    const x = event.clientX - rect.left;
                    const y = event.clientY - rect.top;
                    
                    fetch(`/add_point/${currentMode}`, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({x: x.toFixed(1), y: y.toFixed(1)})
                    });
                });

                fetch('/config')
                    .then(r => r.json())
                    .then(cfg => {
                        fieldConfig = cfg;
                        canvas.width = cfg.field_width;
                        canvas.height = cfg.field_height;
                        requestAnimationFrame(update);
                    })
                    .catch(err => console.error('Ошибка загрузки:', err));
            };

            async function update() {
                if (!fieldConfig) return;
                
                try {
                    const res = await fetch('/status');
                    const status = await res.json();
                    
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                    drawObstacles();
                    drawSpecialMarkers();
                    drawTrail(status.trail);
                    drawCar(status.x, status.y, status.direction);
                    drawPoints(status.points);
                    updateStatusDisplay(status);
                    
                } catch(err) {
                    console.error('Ошибка обновления:', err);
                }
                requestAnimationFrame(update);
            }

            function drawSpecialMarkers() {
                // Стартовая зона
                ctx.fillStyle = '#d3d3d3';
                ctx.fillRect(
                    fieldConfig.start_position[0] - 5,
                    fieldConfig.start_position[1] - 5,
                    10, 10
                );

                // Конечная точка
                ctx.fillStyle = '#87CEEB';
                ctx.fillRect(
                    fieldConfig.end_position[0] - 5,
                    fieldConfig.end_position[1] - 5,
                    10, 10
                );
            }

            function drawObstacles() {
                fieldConfig.obstacles.forEach(obs => {
                    ctx.fillStyle = '#808080';
                    ctx.strokeStyle = '#000';
                    const [type, pos, params] = obs;
                    const [x, y] = pos;
                    
                    if (type === 'circle') {
                        ctx.beginPath();
                        ctx.arc(x, y, params[0], 0, Math.PI*2);
                        ctx.fill();
                        ctx.stroke();
                    }
                    else if (type === 'rectangle') {
                        ctx.fillRect(x, y, params[0], params[1]);
                        ctx.strokeRect(x, y, params[0], params[1]);
                    }
                });
            }

            function drawTrail(trail) {
                if(trail.length < 2) return;
                
                ctx.strokeStyle = 'rgba(255, 0, 0, 0.3)';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(trail[0][0], trail[0][1]);
                
                for(let i = 1; i < trail.length; i++) {
                    ctx.lineTo(trail[i][0], trail[i][1]);
                }
                ctx.stroke();
            }

            function drawCar(x, y, dir) {
                ctx.fillStyle = 'red';
                ctx.beginPath();
                ctx.arc(x, y, 6, 0, Math.PI*2);
                ctx.fill();
                
                // Преобразование направления для отрисовки стрелки
                const angle = Math.PI/2 - (dir * Math.PI / 180);
                ctx.beginPath();
                ctx.moveTo(x, y);
                ctx.lineTo(
                    x + Math.cos(angle)*20,
                    y - Math.sin(angle)*20
                );
                ctx.strokeStyle = '#000';
                ctx.lineWidth = 2;
                ctx.stroke();
            }

            function drawPoints(points) {
                points.forEach((p, i) => {
                    ctx.fillStyle = p.type === 'waypoint' ? '#0a0' : '#00f';
                    ctx.beginPath();
                    ctx.arc(p.x, p.y, 5, 0, Math.PI*2);
                    ctx.fill();
                    
                    ctx.fillStyle = '#000';
                    ctx.font = '12px Arial';
                    ctx.fillText(i+1, p.x + 8, p.y + 3);
                });
            }

            function updateStatusDisplay(status) {
                document.getElementById('posX').textContent = status.x.toFixed(1);
                document.getElementById('posY').textContent = status.y.toFixed(1);
                document.getElementById('speedVal').textContent = status.speed.toFixed(1);
                document.getElementById('directionVal').textContent = Math.round(status.direction);
            }

            function setMode(mode) {
                currentMode = mode;
            }

            function updateVelocity() {
                const speed = parseFloat(document.getElementById('speed').value) || 0;
                const direction = parseFloat(document.getElementById('direction').value) || 0;
                fetch('/set_velocity', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({speed, direction})
                });
            }

            function copyPoints() {
                fetch('/status')
                    .then(r => r.json())
                    .then(data => {
                        const points = data.points.map(p => ({
                            type: p.type,
                            x: p.x,
                            y: p.y
                        }));
                        navigator.clipboard.writeText(JSON.stringify(points, null, 2))
                            .then(() => alert('Точки скопированы!'))
                            .catch(err => console.error('Ошибка копирования:', err));
                    });
            }

            function clearPoints() {
                if(confirm('Удалить все точки?')) {
                    fetch('/clear_points', {
                        method: 'POST'
                    })
                    .then(response => {
                        if(response.ok) {
                            alert('Точки удалены!');
                        }
                    })
                    .catch(err => console.error('Ошибка очистки:', err));
                }
            }

            function resetPosition() {
                if(confirm('Сбросить позицию машинки?')) {
                    fetch('/reset_position', {
                        method: 'POST'
                    })
                    .then(response => {
                        if(response.ok) {
                            alert('Позиция сброшена!');
                        }
                    })
                    .catch(err => console.error('Ошибка сброса:', err));
                }
            }

            function loadPoints() {
                try {
                    const pointsData = JSON.parse(document.getElementById('pointsData').value);
                    fetch('/load_points', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(pointsData)
                    })
                    .then(response => {
                        if(response.ok) {
                            document.getElementById('pointsData').value = '';
                            alert('Точки успешно загружены!');
                        } else {
                            alert('Ошибка загрузки точек!');
                        }
                    });
                } catch(err) {
                    alert('Ошибка формата данных! Проверьте JSON');
                }
            }
        </script>
    </body>
    </html>
    """
    )


if __name__ == "__main__":
    app.run(debug=True)
