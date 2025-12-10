# -------- Step 1: Build Flutter Web --------
FROM cirrusci/flutter:stable AS build

WORKDIR /app
COPY . .

# Flutter web build
RUN flutter pub get
RUN flutter build web --release

# -------- Step 2: Nginx Web Server --------
FROM nginx:alpine

# Copy Flutter build to nginx html folder
COPY --from=build /app/build/web /usr/share/nginx/html

# Expose port
EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
