#!/bin/bash

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Базовые URL
BATTERY_CELL_URL="http://localhost:8006"
CHARGER_URL="http://localhost:8005"
BATTERY_CTRL_URL="http://localhost:8004"
SENSORS_URL="http://localhost:8003"
MONITORING_URL="http://localhost:8002"
COMMS_URL="http://localhost:8001"

# Функция для форматированного вывода JSON
print_json() {
    echo "$1" | python3 -m json.tool 2>/dev/null || echo "$1"
}

# Функция для разделителя
separator() {
    echo -e "\n${CYAN}════════════════════════════════════════════════════════════════════${NC}"
}

# Функция для заголовка
print_header() {
    echo -e "\n${BOLD}${BLUE}>>> $1${NC}"
}

# Функция для успешного результата
print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

# Функция для ошибки
print_error() {
    echo -e "${RED}✗ $1${NC}"
}

# Функция для информации
print_info() {
    echo -e "${YELLOW}ℹ $1${NC}"
}

# Проверка доступности сервисов
check_service() {
    local url=$1
    local name=$2
    if curl -s -f "$url" > /dev/null 2>&1; then
        print_success "$name доступен"
        return 0
    else
        print_error "$name недоступен"
        return 1
    fi
}

# Ожидание запуска сервисов
wait_for_services() {
    print_header "Проверка доступности сервисов"
    sleep 2
    
    check_service "$BATTERY_CELL_URL/status" "Battery Cell (2.2.2)"
    check_service "$CHARGER_URL/status" "Charger (2.2.1)"
    check_service "$BATTERY_CTRL_URL/status" "Battery Controller (2.2)"
    check_service "$SENSORS_URL/readings" "Sensors (2.1)"
    check_service "$MONITORING_URL/health" "Monitoring (2)"
    check_service "$COMMS_URL/status" "Comms (1)"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 1: Battery Cell (2.2.2)
test_battery_cell() {
    print_header "Тестирование Battery Cell Module (2.2.2)"
    
    echo -e "${YELLOW}→ GET /status - Текущее состояние батареи:${NC}"
    response=$(curl -s "$BATTERY_CELL_URL/status")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /discharge - Разряд батареи (500mA на 5 секунд):${NC}"
    response=$(curl -s -X POST "$BATTERY_CELL_URL/discharge?current_ma=500&duration_ms=5000")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /status - Состояние после разряда:${NC}"
    response=$(curl -s "$BATTERY_CELL_URL/status")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /charge - Заряд батареи (2000mA на 3 секунды):${NC}"
    response=$(curl -s -X POST "$BATTERY_CELL_URL/charge?current_ma=2000&duration_ms=3000")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /status - Состояние после заряда:${NC}"
    response=$(curl -s "$BATTERY_CELL_URL/status")
    print_json "$response"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 2: Charger (2.2.1)
test_charger() {
    print_header "Тестирование Charger Module (2.2.1)"
    
    echo -e "${YELLOW}→ GET /status - Статус зарядного устройства:${NC}"
    response=$(curl -s "$CHARGER_URL/status")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /control - Включение зарядного устройства:${NC}"
    response=$(curl -s -X POST "$CHARGER_URL/control" -H "Content-Type: application/json" -d '{"enabled": true}')
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /status - Статус после включения:${NC}"
    response=$(curl -s "$CHARGER_URL/status")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /plug - Имитация отключения зарядного:${NC}"
    response=$(curl -s -X POST "$CHARGER_URL/plug?plugged=false")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /status - Статус после отключения:${NC}"
    response=$(curl -s "$CHARGER_URL/status")
    print_json "$response"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 3: Battery Controller (2.2)
test_battery_controller() {
    print_header "Тестирование Battery Controller Module (2.2)"
    
    echo -e "${YELLOW}→ GET /status - Агрегированный статус батареи:${NC}"
    response=$(curl -s "$BATTERY_CTRL_URL/status")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /control/charge - Включение зарядки через контроллер:${NC}"
    response=$(curl -s -X POST "$BATTERY_CTRL_URL/control/charge?enable=true")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /status - Статус после включения зарядки:${NC}"
    response=$(curl -s "$BATTERY_CTRL_URL/status")
    print_json "$response"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 4: Sensors (2.1)
test_sensors() {
    print_header "Тестирование Sensors Module (2.1)"
    
    echo -e "${YELLOW}→ GET /readings - Показания всех датчиков:${NC}"
    response=$(curl -s "$SENSORS_URL/readings")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /set_max_torque - Установка максимального крутящего момента (50Nm):${NC}"
    response=$(curl -s -X POST "$SENSORS_URL/set_max_torque?max_torque=50")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /readings - Проверка изменений:${NC}"
    response=$(curl -s "$SENSORS_URL/readings")
    print_json "$response"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 5: Monitoring (2)
test_monitoring() {
    print_header "Тестирование Monitoring System (2)"
    
    echo -e "${YELLOW}→ GET /health - Состояние системы мониторинга:${NC}"
    response=$(curl -s "$MONITORING_URL/health")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ GET /telemetry - Сбор полной телеметрии:${NC}"
    response=$(curl -s "$MONITORING_URL/telemetry")
    print_json "$response"
    echo ""
    
    # Проверка на наличие алармов
    alarms=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('alarms', []))" 2>/dev/null)
    if [ "$alarms" != "[]" ] && [ "$alarms" != "" ]; then
        print_error "Обнаружены алармы: $alarms"
    else
        print_success "Алармов не обнаружено"
    fi
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 6: Comms (1)
test_comms() {
    print_header "Тестирование Comms Module (1)"
    
    echo -e "${YELLOW}→ GET /status - Статус модуля связи:${NC}"
    response=$(curl -s "$COMMS_URL/status")
    print_json "$response"
    echo ""
    
    echo -e "${YELLOW}→ POST /alarm - Отправка тестового аларма:${NC}"
    response=$(curl -s -X POST "$COMMS_URL/alarm" -H "Content-Type: application/json" \
        -d '{"alarms": ["TEST_ALARM"], "timestamp": "'$(date -Iseconds)'"}')
    print_json "$response"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Тест 7: Интеграционный тест (все модули вместе)
test_integration() {
    print_header "Интеграционный тест - Полный цикл работы"
    
    echo -e "${BOLD}Сценарий: Пациент делает резкое движение -> аларм -> врач получает уведомление${NC}\n"
    
    # 1. Симулируем резкое движение (через датчики)
    echo -e "${CYAN}1. Симуляция гиперэкстензии (угол 135°):${NC}"
    curl -s -X POST "$SENSORS_URL/simulate_hyperextension" 2>/dev/null || \
    echo -e "${YELLOW}  (имитация через изменение состояния)${NC}"
    
    # 2. Получаем телеметрию с алармом
    echo -e "\n${CYAN}2. Мониторинг обнаруживает проблему:${NC}"
    response=$(curl -s "$MONITORING_URL/telemetry")
    print_json "$response"
    
    # 3. Отправляем команду аварийной остановки
    echo -e "\n${CYAN}3. Врач отправляет команду Emergency Stop:${NC}"
    response=$(curl -s -X POST "$COMMS_URL/alarm" -H "Content-Type: application/json" \
        -d '{"alarms": ["EMERGENCY_STOP"], "source": "doctor", "timestamp": "'$(date -Iseconds)'"}')
    print_json "$response"
    
    # 4. Проверяем статус системы после остановки
    echo -e "\n${CYAN}4. Статус системы после Emergency Stop:${NC}"
    response=$(curl -s "$MONITORING_URL/telemetry")
    print_json "$response"
    echo -e "\n${GREEN}────────────────────────────────────────${NC}"
    echo -e "${YELLOW}Нажмите Enter для следующего теста...${NC}"
    read -r
    echo ""
}

# Функция для непрерывного мониторинга (live mode)
live_monitoring() {
    print_header "LIVE MONITORING MODE (нажмите Ctrl+C для выхода)"
    echo -e "${YELLOW}Обновление каждые 2 секунды...${NC}\n"
    
    while true; do
        clear
        echo -e "${BOLD}${BLUE}════════════════════════════════════════════════════════════════════${NC}"
        echo -e "${BOLD}ЭКЗОСКЕЛЕТ - СИСТЕМА МОНИТОРИНГА В РЕАЛЬНОМ ВРЕМЕНИ${NC}"
        echo -e "${BOLD}${BLUE}════════════════════════════════════════════════════════════════════${NC}"
        echo -e "Время: $(date '+%Y-%m-%d %H:%M:%S')\n"
        
        # Получаем телеметрию
        telemetry=$(curl -s "$MONITORING_URL/telemetry")
        
        # Батарея
        soc=$(echo "$telemetry" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('battery', {}).get('soc', 'N/A'))" 2>/dev/null)
        temp=$(echo "$telemetry" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('battery', {}).get('temperature', 'N/A'))" 2>/dev/null)
        
        # Датчики
        angle=$(echo "$telemetry" | python3 -c "import sys, json; print(json.load(sys.stdin).get('joint_angle', 'N/A'))" 2>/dev/null)
        torque=$(echo "$telemetry" | python3 -c "import sys, json; print(json.load(sys.stdin).get('torque', 'N/A'))" 2>/dev/null)
        
        # Алармы
        alarms=$(echo "$telemetry" | python3 -c "import sys, json; alarms=json.load(sys.stdin).get('alarms', []); print(', '.join(alarms) if alarms else 'Нет')" 2>/dev/null)
        
        # Вывод
        echo -e "${BOLD}🔋 БАТАРЕЯ:${NC}"
        echo -e "  Заряд: ${GREEN}${soc}%${NC}"
        echo -e "  Температура: ${YELLOW}${temp}°C${NC}"
        echo ""
        
        echo -e "${BOLD}🦿 СЕНСОРЫ:${NC}"
        echo -e "  Угол сустава: ${CYAN}${angle}°${NC}"
        echo -e "  Крутящий момент: ${CYAN}${torque} Nm${NC}"
        echo ""
        
        echo -e "${BOLD}🚨 АЛАРМЫ:${NC}"
        if [ "$alarms" != "Нет" ] && [ "$alarms" != "" ]; then
            echo -e "  ${RED}${alarms}${NC}"
        else
            echo -e "  ${GREEN}${alarms}${NC}"
        fi
        
        echo -e "\n${YELLOW}Нажмите Ctrl+C для выхода из режима мониторинга${NC}"
        sleep 2
    done
}

# Главное меню
show_menu() {
    clear
    echo -e "${BOLD}${BLUE}"
    echo "╔═══════════════════════════════════════════════════════════════╗"
    echo "║        ТЕСТИРОВАНИЕ СИСТЕМЫ ЭКЗОСКЕЛЕТА (КИБЕРИММУННАЯ)       ║"
    echo "╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${GREEN}1)${NC} Полное тестирование всех модулей"
    echo -e "${GREEN}2)${NC} Тестирование Battery Cell (2.2.2)"
    echo -e "${GREEN}3)${NC} Тестирование Charger (2.2.1)"
    echo -e "${GREEN}4)${NC} Тестирование Battery Controller (2.2)"
    echo -e "${GREEN}5)${NC} Тестирование Sensors (2.1)"
    echo -e "${GREEN}6)${NC} Тестирование Monitoring (2)"
    echo -e "${GREEN}7)${NC} Тестирование Comms (1)"
    echo -e "${GREEN}8)${NC} Интеграционный тест (полный цикл)"
    echo -e "${GREEN}9)${NC} LIVE мониторинг (режим реального времени)"
    echo -e "${RED}0)${NC} Выход"
    echo ""
    echo -ne "${BOLD}Выберите опцию [0-9]: ${NC}"
}

# Обработка выбора
while true; do
    show_menu
    read choice
    case $choice in
        1)
            wait_for_services
            test_battery_cell
            test_charger
            test_battery_controller
            test_sensors
            test_monitoring
            test_comms
            test_integration
            echo -e "\n${GREEN}✓ Полное тестирование завершено!${NC}"
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        2)
            wait_for_services
            test_battery_cell
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        3)
            wait_for_services
            test_charger
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        4)
            wait_for_services
            test_battery_controller
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        5)
            wait_for_services
            test_sensors
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        6)
            wait_for_services
            test_monitoring
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        7)
            wait_for_services
            test_comms
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        8)
            wait_for_services
            test_integration
            echo -e "\n${YELLOW}Нажмите Enter для продолжения...${NC}"
            read
            ;;
        9)
            wait_for_services
            live_monitoring
            ;;
        0)
            echo -e "\n${GREEN}До свидания!${NC}"
            exit 0
            ;;
        *)
            print_error "Неверный выбор. Пожалуйста, выберите 0-9"
            sleep 1
            ;;
    esac
done
