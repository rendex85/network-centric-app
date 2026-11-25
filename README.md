# network-centric-app

Практическая работа 13 по дисциплине «Архитектура программ»: микросервисное приложение с сетецентрической архитектурой.

Три FastAPI-сервиса, каждый в своём контейнере и со своей базой SQLite в отдельном томе. Друг с другом общаются только по HTTP, по именам сервисов из `docker-compose.yml`.

- `user-service` (порт 5000): CRUD пользователей, `/users`
- `order-service` (порт 5002): CRUD заказов, `/orders`; при создании заказа проверяет пользователя в `user-service` и отправляет уведомление в `notification-service`
- `notification-service` (порт 5001): `POST /notify` пишет уведомление в лог и в свою базу, `GET /notifications` отдаёт список

Вызовы из `order-service` к другим сервисам повторяются при сетевой ошибке (tenacity) и защищены circuit breaker: после двух отказов подряд сервис 15 секунд не вызывается.

## Запуск

```
docker compose up --build -d --wait
```

Пример заказа:

```
curl -X POST http://localhost:5002/orders -H "Content-Type: application/json" -d "{\"user_id\":1,\"item\":\"keyboard\"}"
```

Остановка: `docker compose down` (базы сохраняются в томах), `docker compose down -v` удаляет и их.
