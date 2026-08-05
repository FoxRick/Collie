import WeatherCard from './WeatherCard'
import CalendarCard from './CalendarCard'
import ReminderCard from './ReminderCard'
import EmailCard from './EmailCard'
import ShoppingListCard from './ShoppingListCard'
import RecipeCard from './RecipeCard'
import TravelCard from './TravelCard'
import BudgetCard from './BudgetCard'
import HealthCard from './HealthCard'
import NewsCard from './NewsCard'
import PlanCard from '../plans/PlanCard'
import CapabilityListCard from './CapabilityListCard'
import StatusCard from './StatusCard'
import SuggestionCard from './SuggestionCard'
import GardenerCard from './GardenerCard'
import FilesChangedCard from './FilesChangedCard'

interface Props {
  cardType: string
  cardData: Record<string, unknown>
}

export default function CardRenderer({ cardType, cardData }: Props): React.JSX.Element {
  switch (cardType) {
    case 'weather':
      return <WeatherCard data={cardData} />
    case 'calendar':
      return <CalendarCard data={cardData} />
    case 'reminder':
      return <ReminderCard data={cardData} />
    case 'email':
      return <EmailCard data={cardData} />
    case 'shopping_list':
      return <ShoppingListCard data={cardData} />
    case 'recipe':
      return <RecipeCard data={cardData} />
    case 'travel':
      return <TravelCard data={cardData} />
    case 'budget':
      return <BudgetCard data={cardData} />
    case 'health':
      return <HealthCard data={cardData} />
    case 'news':
      return <NewsCard data={cardData} />
    case 'plan':
      return <PlanCard data={cardData} />
    case 'capability_list':
      return <CapabilityListCard data={cardData} />
    case 'status':
      return <StatusCard data={cardData} />
    case 'profile_suggestion':
      return <SuggestionCard data={cardData} />
    case 'gardener_suggestion':
      return <GardenerCard data={cardData} />
    case 'files_changed':
      return <FilesChangedCard data={cardData} />
    default:
      return <div />
  }
}
