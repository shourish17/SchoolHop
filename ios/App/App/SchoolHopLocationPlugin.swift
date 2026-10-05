import Capacitor
import CoreLocation
import Foundation
import UIKit

@objc(SchoolHopLocationPlugin)
class SchoolHopLocationPlugin: CAPPlugin, CAPBridgedPlugin, CLLocationManagerDelegate {
    let identifier = "SchoolHopLocationPlugin"
    let jsName = "SchoolHopLocation"
    let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "startTripTracking", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stopTripTracking", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "status", returnType: CAPPluginReturnPromise)
    ]

    private let locationManager = CLLocationManager()
    private var tripId: String?
    private var bearerToken: String?
    private var apiBaseUrl: String?
    private var lastPostAt = Date.distantPast
    private var startCall: CAPPluginCall?

    override func load() {
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        locationManager.distanceFilter = 25
        locationManager.pausesLocationUpdatesAutomatically = false
        if #available(iOS 11.0, *) {
            locationManager.showsBackgroundLocationIndicator = true
        }
    }

    @objc func startTripTracking(_ call: CAPPluginCall) {
        guard let tripId = call.getString("tripId"), !tripId.isEmpty else {
            call.reject("Missing tripId")
            return
        }
        guard let token = call.getString("token"), !token.isEmpty else {
            call.reject("Missing bearer token")
            return
        }
        guard let baseUrl = call.getString("apiBaseUrl"),
              let parsedBaseUrl = URL(string: baseUrl),
              ["http", "https"].contains(parsedBaseUrl.scheme?.lowercased()) else {
            call.reject("Missing API base URL")
            return
        }

        stopUpdates()
        self.tripId = tripId
        self.bearerToken = token
        self.apiBaseUrl = baseUrl.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.startCall = call

        DispatchQueue.main.async {
            switch self.locationManager.authorizationStatus {
            case .notDetermined:
                // Start with the foreground prompt, then request Always only for an active driver trip.
                self.locationManager.requestWhenInUseAuthorization()
            case .authorizedWhenInUse:
                self.locationManager.requestAlwaysAuthorization()
                self.beginUpdates()
                call.resolve(["status": "tracking_when_in_use"])
                self.startCall = nil
            case .authorizedAlways:
                self.beginUpdates()
                call.resolve(["status": "tracking"])
                self.startCall = nil
            case .restricted, .denied:
                call.reject("Location permission is required for active driver trip tracking")
                self.startCall = nil
            @unknown default:
                call.reject("Unsupported location permission state")
                self.startCall = nil
            }
        }
    }

    @objc func stopTripTracking(_ call: CAPPluginCall) {
        stopUpdates()
        call.resolve(["status": "stopped"])
    }

    @objc func status(_ call: CAPPluginCall) {
        call.resolve([
            "tracking": tripId != nil,
            "tripId": tripId ?? "",
            "authorization": authorizationLabel(locationManager.authorizationStatus)
        ])
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        handleAuthorization(manager.authorizationStatus)
    }

    func locationManager(_ manager: CLLocationManager, didChangeAuthorization status: CLAuthorizationStatus) {
        handleAuthorization(status)
    }

    private func handleAuthorization(_ status: CLAuthorizationStatus) {
        guard let call = startCall else { return }
        switch status {
        case .authorizedAlways:
            beginUpdates()
            call.resolve(["status": "tracking"])
            startCall = nil
        case .authorizedWhenInUse:
            locationManager.requestAlwaysAuthorization()
            beginUpdates()
            call.resolve(["status": "tracking_when_in_use"])
            startCall = nil
        case .restricted, .denied:
            call.reject("Location permission is required for active driver trip tracking")
            startCall = nil
        case .notDetermined:
            break
        @unknown default:
            call.reject("Unsupported location permission state")
            startCall = nil
        }
    }

    private func beginUpdates() {
        locationManager.allowsBackgroundLocationUpdates = locationManager.authorizationStatus == .authorizedAlways
        locationManager.startUpdatingLocation()
        locationManager.startMonitoringSignificantLocationChanges()
    }

    private func stopUpdates() {
        locationManager.stopUpdatingLocation()
        locationManager.stopMonitoringSignificantLocationChanges()
        tripId = nil
        bearerToken = nil
        apiBaseUrl = nil
        startCall = nil
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let location = locations.last else { return }
        guard Date().timeIntervalSince(lastPostAt) >= 4 else { return }
        lastPostAt = Date()
        post(location: location)
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        notifyListeners("trackingError", data: ["message": error.localizedDescription])
    }

    private func post(location: CLLocation) {
        guard let tripId = tripId, let token = bearerToken, let apiBaseUrl = apiBaseUrl else { return }
        guard let url = URL(string: "\(apiBaseUrl)/api/trips/\(tripId)/locations") else { return }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        let payload: [String: Any] = [
            "latitude": location.coordinate.latitude,
            "longitude": location.coordinate.longitude,
            "accuracy_meters": max(location.horizontalAccuracy, 0),
            "speed_mps": location.speed >= 0 ? location.speed : NSNull(),
            "heading_degrees": location.course >= 0 ? location.course : NSNull(),
            "recorded_at": ISO8601DateFormatter().string(from: location.timestamp)
        ]
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            if let error = error {
                self?.notifyListeners("trackingError", data: ["message": error.localizedDescription])
                return
            }
            guard let httpResponse = response as? HTTPURLResponse else { return }
            if [401, 403, 404, 409].contains(httpResponse.statusCode) {
                DispatchQueue.main.async {
                    self?.stopUpdates()
                    self?.notifyListeners("trackingStopped", data: ["status": httpResponse.statusCode])
                }
            }
        }.resume()
    }

    private func authorizationLabel(_ status: CLAuthorizationStatus) -> String {
        switch status {
        case .notDetermined:
            return "prompt"
        case .restricted:
            return "restricted"
        case .denied:
            return "denied"
        case .authorizedAlways:
            return "always"
        case .authorizedWhenInUse:
            return "when_in_use"
        @unknown default:
            return "unknown"
        }
    }
}
