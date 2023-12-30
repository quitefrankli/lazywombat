//
//  GoalFetcher.swift
//  todoist2
//
//  Created by Frank Li on 28/12/2023.
//

import Foundation
import AWSS3
import Yams
import AWSClientRuntime


struct ResponseJSON : Codable
{
    let goals: Array<Goal>
    let edited: Int
}

struct Goal : Codable
{
    let name: String
    let description: String
    let daily: Int
    init(name: String, description: String, daily: Int)
    {
        self.name = name
        self.description = description
        self.daily = daily
    }
    
    private enum CodingKeys: String, CodingKey {
        case name = "name"
        case description = "description"
        case daily = "daily"
    }
}

class GoalsFetcher : ObservableObject
{
    @Published var goals: Array<Goal> = []
    private var semaphore: DispatchSemaphore = DispatchSemaphore(value: 0)
    private var data: Data?
    
    func async_loader() async throws
    {
        let config: S3Client.S3ClientConfiguration
        let credentials = AWSClientRuntime.Credentials(
            accessKey: "AKIAV2SIJY7TVPWJ2TOE",
            secret: "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"
        )
        let provider = try AWSClientRuntime.StaticCredentialsProvider(credentials)
        config = try S3Client.S3ClientConfiguration(
            region: "ap-southeast-2",
            credentialsProvider: provider
        )
        let client = try S3Client(config: config)
        let input = GetObjectInput(
            bucket: "todoist2",
            key: ".todoist2_goals.yaml"
        )
        let output = try await client.getObject(input: input)
        guard let body = output.body,
              let data2 = try await body.readData() else
        {
            throw fatalError("couldn't read data in body")
        }
        self.data = data2
        semaphore.signal()
    }
    
    func sync_loader() throws
    {
        semaphore = DispatchSemaphore(value: 0)
        Task {
            do {
                try await async_loader()
            } catch {
                print("Error info: \(error)")
            }
        }
        semaphore.wait()
        
        let str = String(decoding: data!, as: UTF8.self)
        let decoder = YAMLDecoder()
        let loadedDictionary = try Yams.load(yaml: str)
        let decoded: ResponseJSON = try decoder.decode(ResponseJSON.self, from: str)
        
        
        self.goals = []
        let today = Date()
        let date_formatter = DateFormatter()
        date_formatter.dateFormat = "dd.MM.yyyy"
        let today_date = date_formatter.string(from: today)
        for goal in decoded.goals
        {
            let daily = Date(timeIntervalSince1970: Double(goal.daily))
            let daily_date = date_formatter.string(from: daily)
            
            if (today_date == daily_date)
            {
                self.goals.append(goal)
            }
        }
    }

    func getEnvironmentVar(_ name: String) -> String? {
        guard let rawValue = getenv(name) else { return nil }
        return String(utf8String: rawValue)
    }
    
    func fetch_goals()
    {
        do {
            setenv("AWS_ACCESS_KEY_ID", "AKIAV2SIJY7TVPWJ2TOE", 1)
            setenv("AWS_SECRET_ACCESS_KEY_ID", "aR8PF4F7hPnQKs3My7R1PPS7ve25LLxD/WE84Pqk", 1)
            try sync_loader()
        } catch {
            print("Error info: \(error)")
        }
//        return self.goals
    }
}
