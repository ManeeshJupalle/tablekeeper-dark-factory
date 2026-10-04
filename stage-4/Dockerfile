# Build stage: compile a fully static binary. Module downloads happen here, during
# `docker build`, which is the only phase with network access.
FROM golang:1.25-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY *.go ./
COPY web ./web
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /tablekeeper .

# Run stage: nothing but the binary. The IANA time zone database is compiled into
# it (time/tzdata), so no OS packages are needed at run time.
FROM scratch
COPY --from=build /tablekeeper /tablekeeper
ENV PORT=8080
EXPOSE 8080
ENTRYPOINT ["/tablekeeper"]
