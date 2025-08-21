/**
 * (C) Copyright IBM Corporation 2015.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package com.ibm.websphere.samples.daytrader.web.servlet;

import static javax.faces.annotation.FacesConfig.Version.JSF_2_3;

import java.io.InputStream;
import java.util.Properties;

import javax.faces.annotation.FacesConfig;
import javax.servlet.ServletContextEvent;
import javax.servlet.ServletContextListener;
import javax.servlet.annotation.WebListener;

import com.ibm.websphere.samples.daytrader.util.Log;
import com.ibm.websphere.samples.daytrader.util.TradeConfig;

@WebListener()
@FacesConfig(version = JSF_2_3)
public class TradeWebContextListener implements ServletContextListener {
  
  
  
    // receieve trade web app startup/shutown events to start(initialized)/stop
    // TradeDirect
    @Override
    public void contextInitialized(ServletContextEvent event) {
      Log.trace("TradeWebContextListener contextInitialized -- initializing TradeDirect");
        
      // Load settings from properties file (if it exists)
      Properties prop = new Properties();
      InputStream stream =  event.getServletContext().getResourceAsStream("/properties/daytrader.properties");
        
      try {
        prop.load(stream);
        System.out.println("Settings from daytrader.properties: " + prop);
        
        if (System.getenv("RUNTIME_MODE") != null) {
          TradeConfig.setRunTimeMode(Integer.parseInt(System.getenv("RUNTIME_MODE")));
        } else {
          TradeConfig.setRunTimeMode(Integer.parseInt(prop.getProperty("runtimeMode")));
        }
        System.out.print("Running in " + TradeConfig.getRunTimeModeNames()[TradeConfig.getRunTimeMode()] + " Mode");
            
        if (System.getenv("ORDER_PROCESSING_MODE") != null) {
          TradeConfig.setOrderProcessingMode(Integer.parseInt(System.getenv("ORDER_PROCESSING_MODE")));
        } else {
          TradeConfig.setOrderProcessingMode(Integer.parseInt(prop.getProperty("orderProcessingMode")));
        }
        System.out.print("Running in " + TradeConfig.getOrderProcessingModeNames()[TradeConfig.getOrderProcessingMode()] + " Order Processing Mode");  
        
        if (System.getenv("MAX_USERS") != null) {
          TradeConfig.setMAX_USERS(Integer.parseInt(System.getenv("MAX_USERS")));
        } else {
          TradeConfig.setMAX_USERS(Integer.parseInt(prop.getProperty("maxUsers")));
        }
        System.out.print("MAX_USERS = " +  prop.getProperty("maxUsers") + " users");
        
        if (System.getenv("MAX_QUOTES") != null) {
          TradeConfig.setMAX_QUOTES(Integer.parseInt(System.getenv("MAX_QUOTES")));
        } else {
          TradeConfig.setMAX_QUOTES(Integer.parseInt(prop.getProperty("maxQuotes")));
        }
        System.out.print("MAX_QUOTES = " +  prop.getProperty("maxQuotes") + " quotes");
       
        if (System.getenv("PUBLISH_QUOTES") != null) {
          TradeConfig.setPublishQuotePriceChange(Boolean.parseBoolean(System.getenv("PUBLISH_QUOTES")));
        } else {
          TradeConfig.setPublishQuotePriceChange(Boolean.parseBoolean(prop.getProperty("publishQuotePriceChange")));
        }
       
        if (System.getenv("DISPLAY_ORDER_ALERTS") != null) {
          TradeConfig.setDisplayOrderAlerts(Boolean.parseBoolean(System.getenv("DISPLAY_ORDER_ALERTS")));
        } else {
          TradeConfig.setDisplayOrderAlerts(Boolean.parseBoolean(prop.getProperty("displayOrderAlerts")));
        }
        if (System.getenv("WEB_INTERFACE") != null) {
          TradeConfig.setWebInterface(Integer.parseInt(System.getenv("WEB_INTERFACE")));
        } else {
          TradeConfig.setWebInterface(Integer.parseInt(prop.getProperty("webInterface")));
        }
        if (System.getenv("LIST_QUOTE_PRICE_CHANGE_FREQUENCY") != null) {
          TradeConfig.setListQuotePriceChangeFrequency(Integer.parseInt(System.getenv("LIST_QUOTE_PRICE_CHANGE_FREQUENCY")));
        } else {
          TradeConfig.setListQuotePriceChangeFrequency(Integer.parseInt(prop.getProperty("listQuotePriceChangeFrequency")));
        }
        
        TradeConfig.setPrimIterations(Integer.parseInt(prop.getProperty("primIterations")));
        TradeConfig.setMarketSummaryInterval(Integer.parseInt(prop.getProperty("marketSummaryInterval")));
        TradeConfig.setLongRun(Boolean.parseBoolean(prop.getProperty("longRun")));
       
      } catch (Exception e) {
        System.out.println("daytrader.properties not found");
      }
       
    }

    @Override
    public void contextDestroyed(ServletContextEvent event) {
        Log.trace("TradeWebContextListener  contextDestroy calling TradeDirect:destroy()");
    }

}
