import os

from crewai import Crew, Process
from dotenv import load_dotenv

from agents import content_agent, email_agent, research_agent, seo_agent
from tasks import content_task, email_task, research_task, seo_task

load_dotenv()

weekly_planning_crew = Crew(
    agents=[research_agent, content_agent, email_agent, seo_agent],
    tasks=[research_task, content_task, email_task, seo_task],
    process=Process.sequential,
    verbose=True,
)

if __name__ == "__main__":
    niche = os.getenv("TARGET_NICHE", "home gadgets")
    result = weekly_planning_crew.kickoff(inputs={"niche": niche})
    print(result)
