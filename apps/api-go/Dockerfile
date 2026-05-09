ARG GO_IMAGE=docker.io/library/golang:1.22-alpine
ARG ALPINE_IMAGE=docker.io/library/alpine:3.20

FROM ${GO_IMAGE} AS build

WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/lodia-api ./cmd/api \
  && CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/lodia-worker ./cmd/worker

FROM ${ALPINE_IMAGE}

RUN addgroup -S lodia && adduser -S -G lodia lodia && apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=build /out/lodia-api /app/lodia-api
COPY --from=build /out/lodia-worker /app/lodia-worker
RUN mkdir -p /objects && chown -R lodia:lodia /objects /app

USER lodia
EXPOSE 8080
ENTRYPOINT ["/app/lodia-api"]
