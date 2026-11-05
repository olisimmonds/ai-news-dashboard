import { useEffect, useState } from "react";

import { getWeather } from "../lib/api";
import type { WeatherLocation } from "../lib/types";
import { Cloud, CloudRain, Sun } from "./icons";

function WeatherMark({ code }: { code: number | null }) {
  if (code === null) return <Cloud size={16} />;
  if (code <= 1) return <Sun size={16} />;
  if (code >= 51) return <CloudRain size={16} />;
  return <Cloud size={16} />;
}

function degrees(value: number | null): string {
  return value === null ? "–" : `${Math.round(value)}°`;
}

function WeatherPlace({ location }: { location: WeatherLocation }) {
  if (!location.available) return null;
  return (
    <li className="weather-place">
      <span className="weather-name">{location.name}</span>
      <span className="weather-condition">
        <WeatherMark code={location.weather_code} />
        <span className="weather-temp">{degrees(location.temperature)}</span>
        <span className="weather-label">{location.condition}</span>
      </span>
      <span className="weather-range">
        H {degrees(location.high)} <span aria-hidden>·</span> L {degrees(location.low)}
        {location.rain_chance !== null ? (
          <>
            {" "}
            <span aria-hidden>·</span> {location.rain_chance}% rain
          </>
        ) : null}
      </span>
    </li>
  );
}

export function WeatherStrip() {
  const [locations, setLocations] = useState<WeatherLocation[]>([]);

  useEffect(() => {
    const controller = new AbortController();

    function load() {
      getWeather({ signal: controller.signal })
        .then((weather) => {
          if (!controller.signal.aborted) setLocations(weather.locations);
        })
        .catch(() => {
          if (!controller.signal.aborted) setLocations([]);
        });
    }

    load();
    const timer = window.setInterval(load, 15 * 60 * 1000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, []);

  const available = locations.filter((location) => location.available);
  if (available.length === 0) return null;

  return (
    <section className="weather-strip" aria-label="Local weather">
      <ul className="weather-list">
        {available.map((location) => (
          <WeatherPlace key={location.id} location={location} />
        ))}
      </ul>
    </section>
  );
}
