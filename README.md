# Экзоскелет

<img width="771" height="449" alt="image" src="https://github.com/user-attachments/assets/63e7dc20-09df-41d5-bcbc-85bca894837a" />   

## Краткое описание проектируемой системы   
Мы разрабатываем систему управления реабилитационным экзоскелетом для людей с отсутствующими нижними и верхними конечностями. Система должна обеспечивать безопасное перемещение пациента (ходьба, вставание,
сидение) по командам из центра дистанционной реабилитации и от планшета врача или локально. Критически важно исключить возможность кибератак или сбоев, которые могут привести к падению человека, нанесению ему травмы или
неконтролируемому движению механизма.<br>  

## Ключевые ценности, ущербы, неприемлемые события:<br>    
<img width="700" height="369" alt="image" src="https://github.com/user-attachments/assets/7dae9fd5-a86a-42b5-8aea-fb58890fc55c" /><br> 

## Цели безопасности:<br>
1. Система управления экзоскелета при любых обстоятельствах принимает только аутентичные команды управления.<br>
2. Система управления экзоскелета при любых обстоятельствах может сделать аварийную остановку.<br>
3. Поток физиологических данных пациента при любых обстоятельствах передается авторизованному и аутентичному пользователю.<br>
4. Параметры движения экзоскелета при любых обстоятельствах не выходят за биомеханический безопасные пределы.<br>
5. Пациенту при любых обстоятельствах не причиняется вред.<br>
6. Модуль отправки тактильных сигналов человеку при любых обстоятельствах передаёт аутентичную информацию о осязаемых предметах.<br>
7. Система мониторинга при любых обстоятельствах может передать сигнал остановки при возникновении непреодолимого препятствия на пути следования.<br>
8. Данные о пациенте при любых обстоятельствах доступны только авторизованным и аутентичным пользователям.<br>
9.  Система движения ног и рук при любых обстоятельствах принимают только аутентичные команды управления.<br>
10.  Экзоскелет при любых обстоятельствах действует в пределах авторизованной зоны.<br>
11.  Система считывания нейронных сигналов при любых обстоятельствах принимает информацию только от пациента.<br><br>

## Особенности:<br>
Экзоскелет управляется из Центра дистанционной реабилитации по беспроводному интерфейсу.<br>
Экзоскелет передаёт телеметрию (состояние пациента, статус устройства) в Центр и на планшет врача.<br>
Аварийная остановка доступна у врача (на планшете).<br><br>
## Предположения безопасности:<br>
1. Только авторизованный мед. персонал имеет право передавать данные.<br>
2. Врачи и операторы Центра дистанционной реабилитации считаются благонадёжными.<br>
3. Аппаратная часть экзоскелета (приводы) исправна.<br><br>
## Контекст:<br>       
<img width="586" height="509" alt="image" src="https://github.com/user-attachments/assets/1b80a17a-4520-4d6f-925d-d1a9765f9213" /><br>
## Основные функциональные сценарии:<br>
<img width="941" height="1289" alt="image" src="https://github.com/user-attachments/assets/06badb48-0182-4b49-a51a-c93b47603fdf" /><br>
## Высокоуровневая архитектура:<br>
<img width="3535" height="1887" alt="image" src="https://github.com/user-attachments/assets/3744f01c-4f32-43f0-a5dd-1911a91baa4c" /> <br>
## Расширенная диаграмма функциональных сценариев:<br>
<img width="1280" height="861" alt="image" src="https://github.com/user-attachments/assets/289657f8-1d58-4806-9c79-516e4b962b9c" /><br>
## Описание подсистем:<br>  
<img width="753" height="515" alt="image" src="https://github.com/user-attachments/assets/cf5845d9-cbf6-4104-859c-5d7b6b7b990d" /><br>
<img width="752" height="513" alt="image" src="https://github.com/user-attachments/assets/08b90fda-0243-45dc-9572-bd1c91959cb1" /><br>
<img width="755" height="94" alt="image" src="https://github.com/user-attachments/assets/b5181f50-131f-4e94-b212-ecbbc6cfda11" /><br>
<img width="756" height="499" alt="image" src="https://github.com/user-attachments/assets/42778481-e0f3-4d76-bf73-13648aaf0d0e" /><br>
## Негативные сценарии:<br>

| № | Наименование угрозы | Описание | Код угрозы |
|:--|:--|:--|:--|
| 1 | Подмена команды через канал связи | Злоумышленник перехватил беспроводной канал связи и внедрил поддельную команду движения, которую система управления экзоскелета приняла как аутентичную. | ЦБ1 |
| 2 | Компрометация системы управления | Вредоносное ПО внедрено в систему управления экзоскелета, в результате чего игнорируются сигналы остановки от системы мониторинга. | ЦБ2 |
| 3 | Подмена данных датчиков-сенсоров | Датчики-сенсоры передали искажённые данные о положении тела в пространстве, система не обнаружила потерю равновесия и не инициировала остановку. | ЦБ2 |
| 4 | Перехват передачи физиологических данных | Поток физиологических данных перенаправляется на сервер злоумышленника. | ЦБ3, ЦБ8 |
| 5 | Подмена нейронных сигналов верхних конечностей | Система считывания нейронных сигналов верхних конечностей получила поддельные сигналы, в результате чего руки экзоскелета выполнили опасное движение. | ЦБ4, ЦБ5, ЦБ11 |
| 6 | Искажение интерпретации осязаемых предметах | Вредоносное ПО изменило алгоритм преобразования информации об осязаемом предмете к тактильным сигналам пациенту, что привело к неправильной интерпретации осязаемого предмета. | ЦБ6 |
| 7 | Неконтролируемое движение гусеницы | Система мониторинга была скомпрометирована, и система движения гусеницы не перестала работать, даже если впереди бетонная стена. | ЦБ7 |
| 8 | Компрометация системы управления конечностей | Вредоносное ПО, проникшее в системы управления конечностей, начинает генерировать случайные команды на движение «в обход» системы считывания нейронных сигналов и системы управления экзоскелета. | ЦБ9 |
| 9 | Изменение геолокации | Компрометация GPS/ГЛОНАСС в системе управления экзоскелета заставляет систему считать, что экзоскелет находится на тренировочной площадке, хотя на самом деле пациент вышел за её пределы. | ЦБ10 |
| 10 | Превышение силы захвата руки | Система контроля силы рук не ограничила усилие захвата, в результате чего экзоскелет травмировал руку пациента. | ЦБ5 |
| 11 | Неконтролируемое движение | Система движения нижней конечности выполнила резкое движение с превышением допустимой скорости. | ЦБ4 |
| 12 | Блокировка открытия экзоскелета | Система открытия и закрытия коляски заблокирована злоумышленником, пациент не может покинуть экзоскелет в аварийной ситуации. | ЦБ2 |
| 13 | Подмена данных о заряде батареи | Контроллер заряда батареи передал ложные данные о высоком уровне заряда, экзоскелет отключился во время движения из-за разряда батареи. | ЦБ2, ЦБ5 |
| 14 | Подмена данных о температуре | Система контроля температуры внутренней части передала ложные данные о нормальной температуре при фактическом перегреве, системы терморегуляции не активировались. | ЦБ5 |
| 15 | Болезненная вибрация | Модуль отправки тактильных сигналов получил команду на максимальную интенсивность вибрации, причинив боль и дискомфорт пациенту. | ЦБ5, ЦБ6 |

<img width="987" height="371" alt="image" src="https://github.com/user-attachments/assets/b3213419-f9ae-4f20-af8b-73e0dfc6fdc4" />

<img width="623" height="298" alt="image" src="https://github.com/user-attachments/assets/14c04694-2a42-4fa1-b57a-e7e94e381624" />

<img width="736" height="255" alt="image" src="https://github.com/user-attachments/assets/82d96f20-3b0c-408c-aba1-c580295d21bf" />

<img width="902" height="255" alt="image" src="https://github.com/user-attachments/assets/6946c2df-7b70-407a-ae7c-d83c96f13595" />

<img width="801" height="255" alt="image" src="https://github.com/user-attachments/assets/f8d203c2-9336-4cbb-bfd2-f67731674321" />

<img width="632" height="355" alt="image" src="https://github.com/user-attachments/assets/79d5ef79-ac08-413f-a8a1-8df87a0d8b9f" />

<img width="596" height="223" alt="image" src="https://github.com/user-attachments/assets/2bda5b92-10f2-40bb-a72d-7102b0d9c208" />

<img width="780" height="298" alt="image" src="https://github.com/user-attachments/assets/824dc365-f6b7-4415-a4c1-bacd4cb18177" />

<img width="731" height="223" alt="image" src="https://github.com/user-attachments/assets/24249ebc-2c78-4c5a-b89f-a8330968acc5" />

<img width="543" height="313" alt="image" src="https://github.com/user-attachments/assets/8b333d9c-d571-40e3-8ebe-f50b45dc2a8d" />

<img width="517" height="226" alt="image" src="https://github.com/user-attachments/assets/1d2bb306-ad5c-4133-914f-353ba1040ff6" />

<img width="491" height="355" alt="image" src="https://github.com/user-attachments/assets/62e0ecd4-563d-4549-ac54-c6bcfdc2f189" />

<img width="640" height="298" alt="image" src="https://github.com/user-attachments/assets/15901da3-35bb-4e5b-b30c-20b9ba618fd0" />

<img width="634" height="236" alt="image" src="https://github.com/user-attachments/assets/a8ba707b-0204-4925-a71e-5a943e436504" />

<img width="438" height="326" alt="image" src="https://github.com/user-attachments/assets/bc16d007-09bf-44a0-a3cd-17fbfd85df3b" />




НОВОЕ

<img width="848" height="384" alt="image" src="https://github.com/user-attachments/assets/dc66b01f-1513-4cfa-a71b-6e002aa62487" />

<img width="926" height="323" alt="image" src="https://github.com/user-attachments/assets/eb982ebc-2a2c-4943-917d-6825a85223bb" />

<img width="1090" height="294" alt="image" src="https://github.com/user-attachments/assets/2747a744-5e4b-499f-b6d3-1b30cf4f4af4" />

<img width="1188" height="281" alt="image" src="https://github.com/user-attachments/assets/b96fce49-c5cf-437f-a27f-688b37ed3867" />

<img width="822" height="294" alt="image" src="https://github.com/user-attachments/assets/0941b2e7-3381-4235-8a06-a945117f8359" />

<img width="1134" height="413" alt="image" src="https://github.com/user-attachments/assets/26076f74-ec88-4ef8-8745-1a76528f7513" />

<img width="1328" height="352" alt="image" src="https://github.com/user-attachments/assets/e64f6254-f61c-49ec-826b-3841fa7aa64b" />

<img width="1054" height="252" alt="image" src="https://github.com/user-attachments/assets/f7b9a0a0-fbb6-4494-9d0e-24f146a2e301" />

<img width="1215" height="323" alt="image" src="https://github.com/user-attachments/assets/de963d11-fca4-45b1-ad0f-117049c8a76f" />

<img width="1008" height="294" alt="image" src="https://github.com/user-attachments/assets/4e88228d-179d-4259-9fc5-06aa7e3060fd" />

<img width="841" height="265" alt="image" src="https://github.com/user-attachments/assets/db84fdeb-d983-4208-9562-99ce0a47b5f2" />

<img width="930" height="384" alt="image" src="https://github.com/user-attachments/assets/447b7665-8b29-4b24-9da5-bbaf37be6827" />

<img width="844" height="252" alt="image" src="https://github.com/user-attachments/assets/dadbcd0b-ec8f-4b9e-a37b-b1bfa80b0993" />

<img width="1045" height="252" alt="image" src="https://github.com/user-attachments/assets/592dbf8f-577e-4c21-92d0-e2c2968bfeda" />

<img width="1060" height="384" alt="image" src="https://github.com/user-attachments/assets/85f655e4-a08b-423f-8cdd-476ff6889076" />
