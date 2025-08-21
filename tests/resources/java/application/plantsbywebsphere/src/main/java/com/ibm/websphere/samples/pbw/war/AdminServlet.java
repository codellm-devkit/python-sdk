//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2003,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.war;

import java.io.IOException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Iterator;

import javax.inject.Inject;
import javax.inject.Named;
import javax.servlet.ServletConfig;
import javax.servlet.ServletContext;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import javax.servlet.http.HttpSession;

import com.ibm.websphere.samples.pbw.bean.BackOrderMgr;
import com.ibm.websphere.samples.pbw.bean.CatalogMgr;
import com.ibm.websphere.samples.pbw.bean.CustomerMgr;
import com.ibm.websphere.samples.pbw.bean.ResetDBBean;
import com.ibm.websphere.samples.pbw.bean.SuppliersBean;
import com.ibm.websphere.samples.pbw.jpa.BackOrder;
import com.ibm.websphere.samples.pbw.jpa.Inventory;
import com.ibm.websphere.samples.pbw.jpa.Supplier;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * Servlet to handle Administration actions
 */
@Named(value = "admin")
@WebServlet("/servlet/AdminServlet")
public class AdminServlet extends HttpServlet {
	/**
	 * 
	 */
	private static final long serialVersionUID = 1L;
	@Inject
	private SuppliersBean suppliers = null;
	@Inject
	private CustomerMgr login;
	@Inject
	private BackOrderMgr backOrderStock = null;

	@Inject
	private CatalogMgr catalog = null;

	@Inject
	private ResetDBBean resetDB;

	/**
	 * @see javax.servlet.Servlet#init(ServletConfig)
	 */
	/**
	 * Servlet initialization.
	 */
	public void init(ServletConfig config) throws ServletException {
		super.init(config);
		// Uncomment the following to generated debug code.
		// Util.setDebug(true);

	}

	/**
	 * Process incoming HTTP GET requests
	 *
	 * @param req
	 *            Object that encapsulates the request to the servlet
	 * @param resp
	 *            Object that encapsulates the response from the servlet
	 */
	public void doGet(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		performTask(req, resp);
	}

	/**
	 * Process incoming HTTP POST requests
	 *
	 * @param req
	 *            Object that encapsulates the request to the servlet
	 * @param resp
	 *            Object that encapsulates the response from the servlet
	 */
	public void doPost(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		performTask(req, resp);
	}

	/**
	 * Method performTask.
	 * 
	 * @param req
	 * @param resp
	 * @throws ServletException
	 * @throws IOException
	 */
	public void performTask(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		String admintype = null;
		admintype = req.getParameter(Util.ATTR_ADMINTYPE);
		Util.debug("inside AdminServlet:performTask. admintype=" + admintype);
		if ((admintype == null) || (admintype.equals(""))) {
			// Invalid Admin
			requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_ADMINHOME);
		}
		if (admintype.equals(Util.ADMIN_BACKORDER)) {
			performBackOrder(req, resp);
		} else if (admintype.equals(Util.ADMIN_SUPPLIERCFG)) {
			performSupplierConfig(req, resp);
		} else if (admintype.equals(Util.ADMIN_POPULATE)) {
			performPopulate(req, resp);
		}
	}

	/**
	 * @param supplierID
	 * @param name
	 * @param street
	 * @param city
	 * @param state
	 * @param zip
	 * @param phone
	 * @param location_url
	 * @return supplierInfo
	 */
	public Supplier updateSupplierInfo(String supplierID,
			String name,
			String street,
			String city,
			String state,
			String zip,
			String phone,
			String location_url) {
		// Only retrieving info for 1 supplier.
		Supplier supplier = null;
		try {
			supplier = suppliers.updateSupplier(supplierID, name, street, city, state, zip, phone, location_url);
		} catch (Exception e) {
			Util.debug("AdminServlet.updateSupplierInfo() - Exception: " + e);
		}
		return (supplier);
	}

	/**
	 * @param req
	 * @param resp
	 * @throws ServletException
	 * @throws IOException
	 */
	public void performSupplierConfig(HttpServletRequest req,
			HttpServletResponse resp) throws ServletException, IOException {
		Supplier supplier = null;
		String action = null;
		action = req.getParameter(Util.ATTR_ACTION);
		if ((action == null) || (action.equals("")))
			action = Util.ACTION_GETSUPPLIER;
		Util.debug("AdminServlet.performSupplierConfig() - action=" + action);
		HttpSession session = req.getSession(true);
		if (action.equals(Util.ACTION_GETSUPPLIER)) {
			// Get supplier info
			try {
				supplier = suppliers.getSupplier();
			} catch (Exception e) {
				Util.debug("AdminServlet.performSupplierConfig() Exception: " + e);
			}
		} else if (action.equals(Util.ACTION_UPDATESUPPLIER)) {
			String supplierID = req.getParameter("supplierid");
			Util.debug("AdminServlet.performSupplierConfig() - supplierid = " + supplierID);
			if ((supplierID != null) && (!supplierID.equals(""))) {
				String name = req.getParameter("name");
				String street = req.getParameter("street");
				String city = req.getParameter("city");
				String state = req.getParameter("state");
				String zip = req.getParameter("zip");
				String phone = req.getParameter("phone");
				String location_url = req.getParameter("location_url");
				supplier = updateSupplierInfo(supplierID, name, street, city, state, zip, phone, location_url);
			}
		} else {
			// Unknown Supplier Config Admin Action so go back to the
			// Administration home page
			sendRedirect(resp, "/PlantsByWebSphere/" + Util.PAGE_ADMINHOME);
		}
		session.setAttribute(Util.ATTR_SUPPLIER, supplier);
		requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_SUPPLIERCFG);
	}

	/**
	 * @param req
	 * @param resp
	 * @throws ServletException
	 * @throws IOException
	 */
	public void performPopulate(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		Populate popDB = new Populate(resetDB, catalog, login, backOrderStock, suppliers);
		popDB.doPopulate();
		sendRedirect(resp, "/PlantsByWebSphere/" + Util.PAGE_HELP);
	}

	/**
	 * Method performBackOrder.
	 * 
	 * @param req
	 * @param resp
	 * @throws ServletException
	 * @throws IOException
	 */
	public void performBackOrder(HttpServletRequest req,
			HttpServletResponse resp) throws ServletException, IOException {
		String action = null;
		action = req.getParameter(Util.ATTR_ACTION);
		if ((action == null) || (action.equals("")))
			action = Util.ACTION_GETBACKORDERS;
		Util.debug("AdminServlet.performBackOrder() - action=" + action);
		HttpSession session = req.getSession(true);
		if (action.equals(Util.ACTION_GETBACKORDERS)) {
			getBackOrders(session);
			requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_BACKADMIN);
		} else if (action.equals(Util.ACTION_UPDATESTOCK)) {
			Util.debug("AdminServlet.performBackOrder() - AdminServlet(performTask):  Update Stock Action");
			String[] backOrderIDs = (String[]) req.getParameterValues("selectedObjectIds");
			if (backOrderIDs != null) {
				for (int i = 0; i < backOrderIDs.length; i++) {
					String backOrderID = backOrderIDs[i];
					Util.debug("AdminServlet.performBackOrder() - Selected BackOrder backOrderID: " + backOrderID);
					try {
						String inventoryID = backOrderStock.getBackOrderInventoryID(backOrderID);
						Util.debug("AdminServlet.performBackOrder() - backOrderID = " + inventoryID);
						int quantity = backOrderStock.getBackOrderQuantity(backOrderID);
						catalog.setItemQuantity(inventoryID, quantity);
						// Update the BackOrder status
						Util.debug("AdminServlet.performBackOrder() - quantity: " + quantity);
						backOrderStock.updateStock(backOrderID, quantity);
					} catch (Exception e) {
						Util.debug("AdminServlet.performBackOrder() - Exception: " + e);
						e.printStackTrace();
					}
				}
			}
			getBackOrders(session);
			requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_BACKADMIN);
		} else if (action.equals(Util.ACTION_CANCEL)) {
			Util.debug("AdminServlet.performBackOrder() - AdminServlet(performTask):  Cancel Action");
			String[] backOrderIDs = (String[]) req.getParameterValues("selectedObjectIds");
			if (backOrderIDs != null) {
				for (int i = 0; i < backOrderIDs.length; i++) {
					String backOrderID = backOrderIDs[i];
					Util.debug("AdminServlet.performBackOrder() - Selected BackOrder backOrderID: " + backOrderID);
					try {
						backOrderStock.deleteBackOrder(backOrderID);
					} catch (Exception e) {
						Util.debug("AdminServlet.performBackOrder() - Exception: " + e);
						e.printStackTrace();
					}
				}
			}
			getBackOrders(session);
			requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_BACKADMIN);
		} else if (action.equals(Util.ACTION_UPDATEQUANTITY)) {
			Util.debug("AdminServlet.performBackOrder() -  Update Quantity Action");
			try {
				String backOrderID = req.getParameter("backOrderID");
				if (backOrderID != null) {
					Util.debug("AdminServlet.performBackOrder() - backOrderID = " + backOrderID);
					String paramquantity = req.getParameter("itemqty");
					if (paramquantity != null) {
						int quantity = new Integer(paramquantity).intValue();
						Util.debug("AdminServlet.performBackOrder() - quantity: " + quantity);
						backOrderStock.setBackOrderQuantity(backOrderID, quantity);
					}
				}
			} catch (Exception e) {
				Util.debug("AdminServlet.performBackOrder() - Exception: " + e);
				e.printStackTrace();
			}
			getBackOrders(session);
			requestDispatch(getServletConfig().getServletContext(), req, resp, Util.PAGE_BACKADMIN);
		} else {
			// Unknown Backup Admin Action so go back to the Administration home
			// page
			sendRedirect(resp, "/PlantsByWebSphere/" + Util.PAGE_ADMINHOME);
		}
	}

	/**
	 * Method getBackOrders.
	 * 
	 * @param session
	 */
	public void getBackOrders(HttpSession session) {
		try {
			// Get the list of back order items.
			Util.debug("AdminServlet.getBackOrders() - Looking for BackOrders");
			Collection<BackOrder> backOrders = backOrderStock.findBackOrders();
			ArrayList<BackOrderItem> backOrderItems = new ArrayList<BackOrderItem>();
			for (BackOrder bo : backOrders) {
				BackOrderItem boi = new BackOrderItem(bo);
				backOrderItems.add(boi);
			}
			Util.debug("AdminServlet.getBackOrders() - BackOrders found!");
			Iterator<BackOrderItem> i = backOrderItems.iterator();
			while (i.hasNext()) {
				BackOrderItem backOrderItem = (BackOrderItem) i.next();
				String backOrderID = backOrderItem.getBackOrderID();
				String inventoryID = backOrderItem.getInventory().getInventoryId();
				// Get the inventory quantity and name for the back order item
				// information.
				Inventory item = catalog.getItemInventory(inventoryID);
				int quantity = item.getQuantity();
				backOrderItem.setInventoryQuantity(quantity);
				String name = item.getName();
				backOrderItem.setName(name);
				// Don't include backorders that have been completed.
				if (!(backOrderItem.getStatus().equals(Util.STATUS_ADDEDSTOCK))) {
					String invID = backOrderItem.getInventory().getInventoryId();
					String supplierOrderID = backOrderItem.getSupplierOrderID();
					String status = backOrderItem.getStatus();
					String lowDate = new Long(backOrderItem.getLowDate()).toString();
					String orderDate = new Long(backOrderItem.getOrderDate()).toString();
					Util.debug("AdminServlet.getBackOrders() - backOrderID = " + backOrderID);
					Util.debug("AdminServlet.getBackOrders() -    supplierOrderID = " + supplierOrderID);
					Util.debug("AdminServlet.getBackOrders() -    invID = " + invID);
					Util.debug("AdminServlet.getBackOrders() -    name = " + name);
					Util.debug("AdminServlet.getBackOrders() -    quantity = " + quantity);
					Util.debug("AdminServlet.getBackOrders() -    status = " + status);
					Util.debug("AdminServlet.getBackOrders() -    lowDate = " + lowDate);
					Util.debug("AdminServlet.getBackOrders() -    orderDate = " + orderDate);
				}
			}
			session.setAttribute("backorderitems", backOrderItems);
		} catch (Exception e) {
			e.printStackTrace();
			Util.debug("AdminServlet.getBackOrders() - RemoteException: " + e);
		}
	}

	/**
	 * Method sendRedirect.
	 * 
	 * @param resp
	 * @param page
	 * @throws ServletException
	 * @throws IOException
	 */
	private void sendRedirect(HttpServletResponse resp, String page) throws ServletException, IOException {
		resp.sendRedirect(resp.encodeRedirectURL(page));
	}

	/**
	 * Method requestDispatch.
	 * 
	 * @param ctx
	 * @param req
	 * @param resp
	 * @param page
	 * @throws ServletException
	 * @throws IOException
	 */
	/**
	 * Request dispatch
	 */
	private void requestDispatch(ServletContext ctx,
			HttpServletRequest req,
			HttpServletResponse resp,
			String page) throws ServletException, IOException {
		resp.setContentType("text/html");
		ctx.getRequestDispatcher(page).forward(req, resp);
	}
}
