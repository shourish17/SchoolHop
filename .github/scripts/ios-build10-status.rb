# Read-only status checks. This file never submits review or changes tester groups.
require 'base64'
require 'json'
require 'net/http'
require 'openssl'
require 'time'
require 'uri'

def token
  key = OpenSSL::PKey::EC.new(ENV.fetch('APP_STORE_CONNECT_API_KEY_P8').gsub('\\n', "\n"))
  now = Time.now.to_i
  header = {'alg' => 'ES256', 'kid' => ENV.fetch('APP_STORE_CONNECT_KEY_ID'), 'typ' => 'JWT'}
  claims = {'iss' => ENV.fetch('APP_STORE_CONNECT_ISSUER_ID'), 'iat' => now - 10, 'exp' => now + 600, 'aud' => 'appstoreconnect-v1'}
  encode = ->(value) { Base64.urlsafe_encode64(value).delete('=') }
  input = encode.call(JSON.generate(header)) + '.' + encode.call(JSON.generate(claims))
  signature = OpenSSL::ASN1.decode(key.dsa_sign_asn1(OpenSSL::Digest::SHA256.digest(input))).value
  binary = signature.map { |part| part.value.to_s(2).rjust(32, "\x00")[-32, 32] }.join
  input + '.' + encode.call(binary)
end

def get(path)
  uri = URI('https://api.appstoreconnect.apple.com' + path)
  request = Net::HTTP::Get.new(uri)
  request['Authorization'] = 'Bearer ' + token
  response = Net::HTTP.start(uri.host, uri.port, use_ssl: true, open_timeout: 30, read_timeout: 60) { |http| http.request(request) }
  raise "App Store Connect read failed: HTTP #{response.code}" unless response.code.to_i.between?(200, 299)
  JSON.parse(response.body)
end

def app_id
  apps = get('/v1/apps?' + URI.encode_www_form({'filter[bundleId]' => 'com.schoolhop.app', 'limit' => '1'})).fetch('data')
  raise 'SchoolHop App Store Connect app missing' unless apps.length == 1
  apps.first.fetch('id')
end

def build(app)
  page = get('/v1/builds?' + URI.encode_www_form({'filter[app]' => app, 'filter[version]' => '10', 'include' => 'preReleaseVersion', 'limit' => '200'}))
  versions = page.fetch('included', []).select { |item| item['type'] == 'preReleaseVersions' }.to_h { |item| [item['id'], item['attributes']] }
  page.fetch('data').find do |item|
    version = versions[item.dig('relationships', 'preReleaseVersion', 'data', 'id')]
    item.dig('attributes', 'version') == '10' && version && version['version'] == '1.0' && version['platform'] == 'IOS'
  end
end

def report(app, item)
  response = get('/v1/builds/' + item.fetch('id') + '?include=preReleaseVersion,buildBetaDetail,betaGroups,betaAppReviewSubmission')
  data = response.fetch('data')
  included = response.fetch('included', [])
  beta = included.find { |entry| entry['type'] == 'buildBetaDetails' }
  review = included.find { |entry| entry['type'] == 'betaAppReviewSubmissions' }
  groups = included.select { |entry| entry['type'] == 'betaGroups' }.map do |entry|
    {'id' => entry['id'], 'name' => entry.dig('attributes', 'name'), 'is_internal' => entry.dig('attributes', 'isInternalGroup')}
  end
  result = {'checked_at' => Time.now.utc.iso8601, 'app_id' => app, 'build_id' => data['id'],
    'bundle_id' => 'com.schoolhop.app', 'version' => '1.0', 'build' => '10',
    'processing_state' => data.dig('attributes', 'processingState'),
    'expired' => data.dig('attributes', 'expired'), 'uploaded_date' => data.dig('attributes', 'uploadedDate'),
    'uses_non_exempt_encryption' => data.dig('attributes', 'usesNonExemptEncryption'),
    'internal_build_state' => beta && beta.dig('attributes', 'internalBuildState'),
    'external_build_state' => beta && beta.dig('attributes', 'externalBuildState'),
    'auto_notify_enabled' => beta && beta.dig('attributes', 'autoNotifyEnabled'),
    'beta_review_state' => review && review.dig('attributes', 'betaReviewState'),
    'groups' => groups, 'tester_or_review_changes_performed' => false}
  File.write('build/receipts/app-store-status.json', JSON.pretty_generate(result) + "\n")
  puts JSON.generate(result)
  result
end

app = app_id
if ARGV.first == 'preflight'
  raise 'Build 1.0 (10) already exists in App Store Connect; stopping to avoid duplicate upload' if build(app)
  puts 'App Store Connect confirms SchoolHop build 1.0 (10) is not already uploaded'
else
  60.times do |attempt|
    item = build(app)
    if item
      result = report(app, item)
      state = result['processing_state']
      raise 'Apple marked the uploaded binary invalid or failed' if ['INVALID', 'FAILED'].include?(state)
      internal = result['internal_build_state']
      break if state == 'VALID' && internal && internal != 'PROCESSING'
    else
      puts JSON.generate({'checked_at' => Time.now.utc.iso8601, 'processing_state' => 'AWAITING_BUILD_RESOURCE', 'attempt' => attempt + 1})
    end
    sleep 30 unless attempt == 59
  end
end
