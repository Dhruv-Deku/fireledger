import SiteHeader from "@/components/SiteHeader";
import HomeView from "@/components/home/HomeView";

export default function Home() {
  return (
    <main className="scrollbar-dark relative flex-1 overflow-y-auto bg-fl-bg">
      <SiteHeader page="home" overlay />
      <HomeView />
    </main>
  );
}
